"""Interactive Telegram bot commands (long polling).

Only Telegram user ids listed in TELEGRAM_ADMIN_IDS get replies; everyone
else is ignored silently. Replies go to the chat the command came from, so
answers requested in the group are visible to all members.
"""

import asyncio
import html
import logging

import httpx

from app.config import get_settings
from app.database import SessionLocal
from app.repositories.account_repository import AccountRepository
from app.services import telegram_service
from app.services.telegram_service import format_ist
from app.services.alert_service import consumed_percent, get_alert_config
from app.services.new_api_service import NewApiError, set_gateway_status

logger = logging.getLogger(__name__)

_HELP = (
    "🤖 <b>Portal Bot Commands</b>\n"
    "────────────────────\n"
    "/usage — pick an account, see full metrics\n"
    "/usage &lt;name&gt; — details for matching accounts\n"
    "/alerts — accounts at or above the lowest configured threshold\n"
    "/disabled — channels currently disabled\n"
    "/enable — pick a disabled channel to re-enable\n"
    "/disable — pick an enabled channel to disable\n"
    "/help — this message"
)


def _bar(percent: float, slots: int = 10) -> str:
    filled = min(slots, round(percent / 100 * slots))
    return "▰" * filled + "▱" * (slots - filled)


def _percent(account) -> float | None:
    return consumed_percent(account)


def _credit_text(account, value: float | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    code = (account.credits_currency or "USD").upper()
    amount = f"{value:,.{digits}f}"
    if code == "INR":
        return f"₹{amount}"
    if code == "USD":
        return f"${amount}"
    return f"{amount} {code}"


def _account_card(account) -> str:
    percent = _percent(account)
    status = "🟢 enabled" if account.new_api_status == 1 else "🔴 disabled" if account.new_api_status else "—"
    weight = account.new_api_weight if account.new_api_weight is not None else "—"
    priority = account.new_api_priority if account.new_api_priority is not None else "—"
    lines = [
        f"🏦 <b>{html.escape(account.name)}</b>",
        "────────────────────",
        f"📡 Channel    {html.escape(account.new_api_name or '—')} · {account.new_api_gateway or 'O1'}",
    ]
    gateway_labels = (account.new_api_gateway or "O1").split("+")
    if "O1" in gateway_labels:
        lines.append(f"💸 O1 spend   ${(account.new_api_cost_o1_usd or 0):,.2f}")
    if "O2" in gateway_labels:
        lines.append(f"💸 O2 spend   ${(account.new_api_cost_o2_usd or 0):,.2f}")
    lines.extend(
        [
            f"Σ  Total      <b>${(account.new_api_cost_usd or 0):,.2f}</b>",
            f"💳 Azure credits  {_credit_text(account, account.credits_remaining)} left of {_credit_text(account, account.credits_limit)}",
        ]
    )
    if percent is not None:
        lines.append(f"📊 {_bar(percent)}  <b>{percent:.1f}% consumed</b>")
    lines.append(f"⚙️ Gateway    {status} · weight {weight} · priority {priority}")
    if account.new_api_synced_at:
        lines.append(f"🕒 Synced     {format_ist(account.new_api_synced_at)}")
    return "\n".join(lines)


def _account_keyboard(accounts, prefix: str = "acct") -> dict:
    # Highest spend-vs-credits first; disabled gateways pushed to the end.
    ordered = sorted(accounts, key=lambda a: (a.new_api_status != 1, -(_percent(a) or 0)))
    buttons = []
    row = []
    for account in ordered:
        percent = _percent(account)
        suffix = f" · {percent:.0f}%" if percent is not None else ""
        marker = "" if account.new_api_status == 1 else "⛔ "
        row.append({"text": f"{marker}{account.name}{suffix}", "callback_data": f"{prefix}:{account.id}"})
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([{"text": "✖️ Cancel", "callback_data": "cancel"}])
    return {"inline_keyboard": buttons}


async def _cmd_usage(query: str) -> tuple[str, dict | None]:
    async with SessionLocal() as session:
        accounts = await AccountRepository(session).list_all()

    if query:
        needle = query.lower()
        matches = [
            a
            for a in accounts
            if needle in a.name.lower()
            or needle in (a.new_api_name or "").lower()
            or needle in (a.new_api_tag or "").lower()
        ]
        if not matches:
            return f"No account matching “{html.escape(query)}”.", None
        if len(matches) == 1:
            return _account_card(matches[0]), None
        return (
            f"🔎 <b>{len(matches)} accounts match “{html.escape(query)}”</b> — pick one:",
            _account_keyboard(matches),
        )

    header = f"📋 <b>Accounts ({len(accounts)})</b> — pick one for full metrics:"
    return header, _account_keyboard(accounts)


async def _cmd_alerts() -> str:
    async with SessionLocal() as session:
        accounts = await AccountRepository(session).list_all()
        config = await get_alert_config(session)
    floor = min(config["thresholds"]) if config["thresholds"] else 75
    hot = sorted((a for a in accounts if (_percent(a) or 0) >= floor), key=lambda a: _percent(a) or 0, reverse=True)
    if not hot:
        return f"✅ All accounts are below {floor}% of their Azure credits."
    lines = [f"🚨 <b>Accounts at or above {floor}% of Azure credits</b>", "────────────────────"]
    warn_at = max(config["thresholds"]) if config["thresholds"] else 95
    for account in hot:
        percent = _percent(account)
        icon = "🚨" if percent >= warn_at else "🟠"
        remaining = _credit_text(account, account.credits_remaining, digits=0)
        lines.append(f"{icon} {percent:5.1f}%  {html.escape(account.name)}  ({remaining} left)")
    return "\n".join(lines)


async def _cmd_disabled() -> str:
    async with SessionLocal() as session:
        accounts = await AccountRepository(session).list_all()
    disabled = [a for a in accounts if a.new_api_status not in (None, 1)]
    if not disabled:
        return "✅ No disabled gateway channels."
    lines = ["⛔ <b>Disabled gateway channels</b>", "────────────────────"]
    for account in disabled:
        percent = _percent(account)
        extra = f" · {percent:.0f}% consumed" if percent is not None else ""
        lines.append(f"🔴 {html.escape(account.name)} — {html.escape(account.new_api_name or '—')}{extra}")
    lines.append("\nRe-enable with /enable or from the portal's Accounts page.")
    return "\n".join(lines)


async def _cmd_toggle_picker(enable: bool) -> tuple[str, dict | None]:
    async with SessionLocal() as session:
        accounts = await AccountRepository(session).list_all()
    if enable:
        candidates = [a for a in accounts if a.new_api_status not in (None, 1) and a.new_api_gateway]
        if not candidates:
            return "✅ No disabled gateway channels to enable.", None
        return "🟢 <b>Enable gateway</b> — pick a channel:", _account_keyboard(candidates, prefix="en")
    candidates = [a for a in accounts if a.new_api_status == 1 and a.new_api_gateway]
    if not candidates:
        return "⛔ No enabled gateway channels to disable.", None
    return "⛔ <b>Disable gateway</b> — pick a channel:", _account_keyboard(candidates, prefix="dis")


async def _handle_command(text: str) -> tuple[str, dict | None]:
    parts = text.strip().split(maxsplit=1)
    command = parts[0].split("@")[0].lower()
    argument = parts[1].strip() if len(parts) > 1 else ""
    if command == "/usage":
        return await _cmd_usage(argument)
    if command == "/alerts":
        return await _cmd_alerts(), None
    if command == "/disabled":
        return await _cmd_disabled(), None
    if command == "/enable":
        return await _cmd_toggle_picker(enable=True)
    if command == "/disable":
        return await _cmd_toggle_picker(enable=False)
    return _HELP, None


async def _process_callback(callback: dict) -> None:
    sender = str((callback.get("from") or {}).get("id") or "")
    message = callback.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")
    data = callback.get("data") or ""
    await telegram_service.answer_callback_query(callback.get("id") or "")
    if chat_id is None or sender not in get_settings().telegram_admin_id_set:
        return
    if data == "cancel":
        if message_id is not None:
            await telegram_service.delete_message(chat_id, message_id)
        return
    prefix, _, raw_id = data.partition(":")
    if prefix not in ("acct", "en", "dis") or not raw_id.isdigit():
        return
    account_id = int(raw_id)
    try:
        async with SessionLocal() as session:
            if prefix == "acct":
                account = await AccountRepository(session).get(account_id)
                text = _account_card(account) if account else "That account no longer exists."
            else:
                enable = prefix == "en"
                try:
                    result = await set_gateway_status(session, account_id, 1 if enable else 2)
                    account = await AccountRepository(session).get(account_id)
                    if result.get("status") != "ok":
                        errors = "; ".join(f"{k}: {v}" for k, v in (result.get("errors") or {}).items())
                        headline = "⚠️ <b>Partial gateway update</b>"
                        text = f"{headline}\n<i>{html.escape(errors)}</i>\n\n{_account_card(account)}"
                    else:
                        headline = "🟢 <b>Gateway Enabled</b>" if enable else "⛔ <b>Gateway Disabled</b>"
                        text = f"{headline}\n\n{_account_card(account)}"
                except NewApiError as exc:
                    text = f"❌ Could not update the gateway: {html.escape(str(exc))}"
        # Replace the picker message with the result so the button list disappears.
        if message_id is not None:
            await telegram_service.edit_message_text(chat_id, message_id, text)
        else:
            await telegram_service.send_message(text, chat_id=chat_id)
    except Exception:
        logger.warning("Bot callback failed: %s", data, exc_info=True)


async def _process_update(update: dict) -> None:
    if update.get("callback_query"):
        await _process_callback(update["callback_query"])
        return
    message = update.get("message") or {}
    text = message.get("text") or ""
    if not text.startswith("/"):
        return
    sender = str((message.get("from") or {}).get("id") or "")
    chat_id = (message.get("chat") or {}).get("id")
    if chat_id is None:
        return
    if sender not in get_settings().telegram_admin_id_set:
        logger.info("Ignoring bot command from non-admin %s", sender)
        return
    try:
        reply, keyboard = await _handle_command(text)
        await telegram_service.send_message(reply, chat_id=chat_id, reply_markup=keyboard)
    except Exception:
        logger.warning("Bot command failed: %s", text, exc_info=True)


async def run_bot_polling() -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        logger.info("Telegram bot token not set; bot commands disabled")
        return
    offset: int | None = None
    logger.info("Telegram bot polling started")
    while True:
        try:
            async with httpx.AsyncClient(timeout=40) as client:
                response = await client.get(
                    f"https://api.telegram.org/bot{settings.telegram_bot_token}/getUpdates",
                    params={
                        "timeout": 30,
                        "allowed_updates": '["message","callback_query"]',
                        **({"offset": offset} if offset is not None else {}),
                    },
                )
            updates = response.json().get("result") or []
            for update in updates:
                offset = update["update_id"] + 1
                await _process_update(update)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Telegram polling error", exc_info=True)
            await asyncio.sleep(5)
