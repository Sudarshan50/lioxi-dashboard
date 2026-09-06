"""Interactive Telegram bot commands (webhook).

Group chat: /live, /help, /start for everyone.
Private chat: admin commands for TELEGRAM_ADMIN_IDS only.
"""

import asyncio
import hashlib
import hmac
import html
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.config import get_settings
from app.database import SessionLocal
from app.repositories.account_repository import AccountRepository
from app.services import telegram_service
from app.services.alert_service import consumed_percent, credit_grant_usd, get_alert_config
from app.services.new_api_service import NewApiError, set_gateway_status

logger = logging.getLogger(__name__)

# Same 12% rate the portal uses for amount payable / projected income.
PROJECTED_INCOME_RATE = 0.12
UNTAGGED_PERSON = "Untagged"
LIVE_MESSAGE_TTL_SECONDS = 30

_PUBLIC_COMMANDS = {"/live", "/help", "/start"}
_pending_deletes: dict[tuple[str, int], asyncio.Task] = {}

_PUBLIC_HELP = (
    "👋 <b>Commands</b>\n"
    "────────────────────\n"
    "/live — pick a name, see live usage (auto-deletes in 30s)\n"
    "/live &lt;name&gt; — live usage for that person\n"
    "/help — this message"
)

_HELP = (
    "👋 <b>Bot commands</b>\n"
    "────────────────────\n"
    "/live — pick a name, see live usage (everyone in the group; auto-deletes in 30s)\n"
    "/live &lt;name&gt; — live usage for that person\n"
    "/people — pick a name, then channel usage for that person\n"
    "/people &lt;name&gt; — channel usage for that person\n"
    "/usage — older per-account view (still works)\n"
    "/usage &lt;name&gt; — older details for one account\n"
    "/alerts — accounts running high\n"
    "/test — examples of /usage, /alerts, and auto-disable\n"
    "/disabled — paused accounts\n"
    "/enable — turn a paused account back on\n"
    "/disable — pause an account\n"
    "/help — this message"
)


def chat_is_allowed(
    chat: dict | None,
    sender: str,
    *,
    admin_ids: set[str],
    owner_id: str | set[str],
    group_id: str,
) -> bool:
    """True only for an admin in a private chat. Group is public-only."""
    del owner_id, group_id
    if not sender or sender not in admin_ids:
        return False
    return str((chat or {}).get("type") or "") == "private"


def _chat_allowed(chat: dict | None, sender: str) -> bool:
    settings = get_settings()
    return chat_is_allowed(
        chat,
        sender,
        admin_ids=settings.telegram_admin_id_set,
        owner_id=settings.telegram_owner_id_set,
        group_id=str(settings.telegram_chat_id or "").strip(),
    )


def _group_open(chat: dict | None) -> bool:
    settings = get_settings()
    group_id = str(settings.telegram_chat_id or "").strip()
    info = chat or {}
    return str(info.get("type") or "") in ("group", "supergroup") and str(info.get("id") or "") == group_id


def _cancel_self_destruct(chat_id: str | int | None, message_id: int | None) -> None:
    if chat_id is None or message_id is None:
        return
    task = _pending_deletes.pop((str(chat_id), int(message_id)), None)
    if task is not None:
        task.cancel()


def cancel_all_self_destructs() -> int:
    pending = list(_pending_deletes.items())
    _pending_deletes.clear()
    for _key, task in pending:
        task.cancel()
    return len(pending)


async def start_clear_group_chat() -> dict:
    cancel_all_self_destructs()
    return await telegram_service.start_clear_group_chat()


def _message_ids(*message_ids: int | None | list[int | None] | tuple[int | None, ...]) -> list[int]:
    ids: list[int] = []
    for raw in message_ids:
        if raw is None:
            continue
        if isinstance(raw, (list, tuple, set)):
            ids.extend(_message_ids(*raw))
        else:
            ids.append(int(raw))
    return list(dict.fromkeys(ids))


def schedule_self_destruct(
    chat_id: str | int | None,
    *message_ids: int | None | list[int | None] | tuple[int | None, ...],
    delay: float = LIVE_MESSAGE_TTL_SECONDS,
) -> asyncio.Task | None:
    """Delete Telegram messages after `delay` seconds. Replaces any prior timer for those messages."""
    ids = _message_ids(*message_ids)
    if chat_id is None or not ids:
        return None
    for message_id in ids:
        _cancel_self_destruct(chat_id, message_id)

    async def _delete() -> None:
        try:
            await asyncio.sleep(delay)
            for message_id in ids:
                await telegram_service.delete_message(chat_id, message_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Live message self-destruct failed: %s/%s", chat_id, ids, exc_info=True)
        finally:
            task = asyncio.current_task()
            for message_id in ids:
                key = (str(chat_id), message_id)
                if _pending_deletes.get(key) is task:
                    _pending_deletes.pop(key, None)

    task = asyncio.create_task(_delete())
    for message_id in ids:
        _pending_deletes[(str(chat_id), message_id)] = task
    return task


def _live_ttl_note() -> str:
    return f"\n\n<i>⏱ Disappears in {LIVE_MESSAGE_TTL_SECONDS}s</i>"


def _command_allowed(chat: dict | None, sender: str, command: str) -> bool:
    if _chat_allowed(chat, sender):
        return True
    return command in _PUBLIC_COMMANDS and _group_open(chat)


def _callback_allowed(chat: dict | None, sender: str, data: str) -> bool:
    if _chat_allowed(chat, sender):
        return True
    return _group_open(chat) and (
        data.startswith("live:") or data == "cancel" or data.startswith("cancel:")
    )


def _bar(percent: float, slots: int = 10) -> str:
    filled = min(slots, round(percent / 100 * slots))
    return "▰" * filled + "▱" * (slots - filled)


def _percent(account) -> float | None:
    return consumed_percent(account)


def _spend(account) -> float:
    return max(float(account.new_api_cost_usd or 0), 0.0)


def _projected(spend: float) -> float:
    return round(spend * PROJECTED_INCOME_RATE, 2)


def _money(value: float) -> str:
    return f"${value:,.2f}"


def _is_live(account) -> bool:
    return account.new_api_status == 1


def _person_label(account) -> str:
    tag = (getattr(account, "owner_tag", None) or "").strip()
    return tag or UNTAGGED_PERSON


def _sort_accounts(accounts):
    return sorted(accounts, key=lambda a: (not _is_live(a), -_spend(a), (a.name or "").lower()))


def _people_groups(accounts) -> list[tuple[str, list]]:
    grouped: dict[str, list] = {}
    for account in accounts:
        grouped.setdefault(_person_label(account), []).append(account)
    items = [(tag, _sort_accounts(rows)) for tag, rows in grouped.items()]

    def sort_key(item: tuple[str, list]):
        tag, rows = item
        all_paused = all(not _is_live(account) for account in rows)
        spend = sum(_spend(account) for account in rows)
        return (tag == UNTAGGED_PERSON, all_paused, -spend, tag.lower())

    return sorted(items, key=sort_key)


def _live_groups(accounts) -> list[tuple[str, list]]:
    return _people_groups([account for account in accounts if _is_live(account)])


def _person_token(tag: str) -> str:
    return hashlib.sha1(tag.encode("utf-8")).hexdigest()[:12]


def _group_by_token(groups: list[tuple[str, list]], token: str) -> tuple[str, list] | None:
    for tag, rows in groups:
        if _person_token(tag) == token:
            return tag, rows
    return None


def _people_keyboard(
    groups: list[tuple[str, list]],
    selected: list[int] | None = None,
    prefix: str = "who",
    source_message_id: int | None = None,
) -> dict:
    indexes = selected if selected is not None else list(range(len(groups)))
    suffix = f":{int(source_message_id)}" if source_message_id is not None else ""
    buttons = []
    row = []
    for index in indexes:
        tag, rows = groups[index]
        marker = "" if any(_is_live(account) for account in rows) else "⏸ "
        label = f"{marker}{tag}"
        if len(label) > 60:
            label = f"{marker}{tag[:56]}…"
        row.append({"text": label, "callback_data": f"{prefix}:{_person_token(tag)}{suffix}"})
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([{"text": "✖️ Cancel", "callback_data": f"cancel{suffix}"}])
    return {"inline_keyboard": buttons}


_EMPTY_KEYBOARD = {"inline_keyboard": []}


def _channel_block(account, index: int | None = None) -> str:
    gateway = account.new_api_gateway or "O1"
    labels = [part.strip() for part in gateway.split("+") if part.strip()] or ["O1"]
    grant = credit_grant_usd(account)
    percent = _percent(account)
    title = html.escape(account.new_api_name or account.name or "channel")
    extra = " · paused" if not _is_live(account) else ""
    prefix = f"<b>{index}.</b> " if index is not None else ""
    lines = [f"{prefix}<code>{title}</code> · {html.escape(gateway)}{extra}"]
    if "O1" in labels:
        lines.append(f"O1 spend: {_money(float(account.new_api_cost_o1_usd or 0))}")
    if "O2" in labels:
        lines.append(f"O2 spend: {_money(float(account.new_api_cost_o2_usd or 0))}")
    lines.append(f"Total: <b>{_money(_spend(account))}</b>")
    lines.append(f"Azure grant: {_money(grant) if grant else '—'}")
    if percent is not None:
        lines.append(f"{_bar(percent)}  {percent:.1f}%")
    return "\n".join(lines)


def _person_card(tag: str, accounts, *, include_projected: bool = True) -> str:
    rows = _sort_accounts(accounts)
    spend = sum(_spend(account) for account in rows)
    count = len(rows)
    title = f"👤 <b>{html.escape(tag)}</b> · {count} channel{'s' if count != 1 else ''}"
    header = (
        f"💰 Projected income: <b>{_money(_projected(spend))}</b>\n{title}"
        if include_projected
        else title
    )
    blocks = [_channel_block(account, index) for index, account in enumerate(rows, start=1)]
    return "\n\n".join([header, *blocks])


def _live_card(tag: str, accounts) -> str:
    return _person_card(tag, [account for account in accounts if _is_live(account)], include_projected=False) + _live_ttl_note()


def _account_who(account) -> str:
    owner = (getattr(account, "owner_tag", None) or "").strip()
    name = (account.name or "").strip()
    if owner and name and owner != name:
        return f"{html.escape(owner)} · {html.escape(name)}"
    return html.escape(owner or name or "account")


def _account_card(account) -> str:
    return f"👤 <b>{_account_who(account)}</b>\n\n{_channel_block(account)}"


def _channel_report(accounts, subtitle: str, numbered: bool = True) -> str:
    rows = list(accounts)
    blocks = [
        _channel_block(account, index if numbered else None)
        for index, account in enumerate(rows, start=1)
    ]
    return "\n\n".join([subtitle, *blocks])


def _split_telegram(text: str, limit: int = 4000) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for block in text.split("\n\n"):
        pieces = [block[i : i + limit] for i in range(0, max(len(block), 1), limit)]
        for piece in pieces:
            extra = len(piece) + (2 if current else 0)
            if current and size + extra > limit:
                chunks.append("\n\n".join(current))
                current = [piece]
                size = len(piece)
            else:
                current.append(piece)
                size += extra
    if current:
        chunks.append("\n\n".join(current))
    return chunks or [text]


async def _cmd_people(query: str) -> tuple[str, dict | None]:
    async with SessionLocal() as session:
        accounts = await AccountRepository(session).list_all()
    groups = _people_groups(accounts)
    if not groups:
        return "No people to show yet.", None

    if query:
        needle = query.lower()
        matches = [index for index, (tag, _rows) in enumerate(groups) if needle in tag.lower()]
        if not matches:
            return f"No person matching “{html.escape(query)}”.", None
        if len(matches) == 1:
            tag, rows = groups[matches[0]]
            return _person_card(tag, rows), None
        return (
            f"Whose report? {len(matches)} names match “{html.escape(query)}”.",
            _people_keyboard(groups, matches),
        )

    return "Whose report?", _people_keyboard(groups)


async def _cmd_live(query: str, source_message_id: int | None = None) -> tuple[str, dict | None]:
    async with SessionLocal() as session:
        accounts = await AccountRepository(session).list_all()
    groups = _live_groups(accounts)
    if not groups:
        return f"No live channels to show right now.{_live_ttl_note()}", None

    if query:
        needle = query.lower()
        matches = [index for index, (tag, _rows) in enumerate(groups) if needle in tag.lower()]
        if not matches:
            return f"No person matching “{html.escape(query)}”.{_live_ttl_note()}", None
        if len(matches) == 1:
            tag, rows = groups[matches[0]]
            return _live_card(tag, rows), None
        return (
            f"Whose live usage? {len(matches)} names match “{html.escape(query)}”.{_live_ttl_note()}",
            _people_keyboard(groups, matches, prefix="live", source_message_id=source_message_id),
        )

    return (
        f"Whose live usage?{_live_ttl_note()}",
        _people_keyboard(groups, prefix="live", source_message_id=source_message_id),
    )


def _account_keyboard(accounts, prefix: str = "acct") -> dict:
    # Highest spend-vs-credits first; disabled gateways pushed to the end.
    ordered = sorted(accounts, key=lambda a: (a.new_api_status != 1, -(_percent(a) or 0)))
    buttons = []
    row = []
    for account in ordered:
        percent = _percent(account)
        suffix = f" · {percent:.0f}%" if percent is not None else ""
        marker = "" if account.new_api_status == 1 else "⛔ "
        name = (account.name or "account").strip() or "account"
        if len(name) > 40:
            name = f"{name[:37]}…"
        row.append({"text": f"{marker}{name}{suffix}", "callback_data": f"{prefix}:{account.id}"})
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
            if needle in (a.name or "").lower()
            or needle in (a.new_api_name or "").lower()
            or needle in (a.new_api_tag or "").lower()
        ]
        if not matches:
            return f"No account matching “{html.escape(query)}”.", None
        if len(matches) == 1:
            return _account_card(matches[0]), None
        return (
            f"Which account? {len(matches)} match “{html.escape(query)}”.",
            _account_keyboard(matches),
        )

    return "Which account?", _account_keyboard(accounts)


async def _cmd_alerts() -> str:
    async with SessionLocal() as session:
        accounts = await AccountRepository(session).list_all()
        config = await get_alert_config(session)
    floor = min(config["thresholds"]) if config["thresholds"] else 75
    live = [account for account in accounts if _is_live(account)]
    hot = sorted(
        (account for account in live if (_percent(account) or 0) >= floor),
        key=lambda account: _percent(account) or 0,
        reverse=True,
    )
    if not hot:
        return f"Nothing is at or above {floor}% right now."
    return _channel_report(
        hot,
        f"⚠ <b>At or above {floor}%</b> · {len(hot)} channel{'s' if len(hot) != 1 else ''}",
        numbered=False,
    )


async def _cmd_disabled() -> str:
    async with SessionLocal() as session:
        accounts = await AccountRepository(session).list_all()
    disabled = _sort_accounts([account for account in accounts if account.new_api_status not in (None, 1)])
    if not disabled:
        return "No paused channels."
    return _channel_report(
        disabled,
        f"⏸ <b>Paused</b> · {len(disabled)} channel{'s' if len(disabled) != 1 else ''}",
    )


def _example_account(**overrides):
    from types import SimpleNamespace

    row = {
        "name": "Gaurav1",
        "owner_tag": "Gaurav",
        "new_api_name": "kimi-k3-500k-proxy-20",
        "new_api_gateway": "O1",
        "new_api_cost_usd": 3180.53,
        "new_api_cost_o1_usd": 3180.53,
        "new_api_cost_o2_usd": 0,
        "new_api_status": 1,
        "credits_limit": 10000,
        "credits_currency": "USD",
    }
    row.update(overrides)
    return SimpleNamespace(**row)


def _cmd_test() -> str:
    from app.services.alert_service import _format_exhausted_alert

    usage = _example_account()
    hot = _example_account(
        name="Gaurav2",
        new_api_name="kimi-k3-500k-proxy-21",
        new_api_cost_usd=8000,
        new_api_cost_o1_usd=8000,
    )
    stopped = _example_account(
        name="Gaurav3",
        new_api_name="kimi-k3-500k-proxy-22",
        new_api_cost_usd=10250,
        new_api_cost_o1_usd=10250,
        new_api_status=2,
    )
    return (
        "<i>Example only — not live data</i>\n\n"
        "<b>Example — /usage</b>\n\n"
        f"{_account_card(usage)}\n\n"
        "────────\n\n"
        "<b>Example — /alerts</b>\n\n"
        f"{_channel_report([hot], '⚠ <b>At or above 75%</b> · 1 channel', numbered=False)}\n\n"
        "────────\n\n"
        "<b>Example — auto-disable</b>\n\n"
        f"{_format_exhausted_alert(stopped, {'O1': True}, None)}"
    )


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


async def _handle_command(
    text: str, *, public: bool = False, source_message_id: int | None = None
) -> tuple[str, dict | None]:
    parts = text.strip().split(maxsplit=1)
    command = parts[0].split("@")[0].lower()
    argument = parts[1].strip() if len(parts) > 1 else ""
    if command in ("/help", "/start"):
        return (_PUBLIC_HELP if public else _HELP), None
    if command == "/live":
        return await _cmd_live(argument, source_message_id=source_message_id)
    if public:
        return _PUBLIC_HELP, None
    if command == "/people":
        return await _cmd_people(argument)
    if command == "/usage":
        return await _cmd_usage(argument)
    if command == "/alerts":
        return await _cmd_alerts(), None
    if command == "/test":
        return _cmd_test(), None
    if command == "/disabled":
        return await _cmd_disabled(), None
    if command == "/enable":
        return await _cmd_toggle_picker(enable=True)
    if command == "/disable":
        return await _cmd_toggle_picker(enable=False)
    return _HELP, None


async def _replace_message(
    chat_id,
    message_id: int | None,
    text: str,
    *,
    ephemeral: bool = False,
    source_id: int | None = None,
) -> None:
    chunks = _split_telegram(text)
    if message_id is not None:
        try:
            await telegram_service.edit_message_text(
                chat_id, message_id, chunks[0], reply_markup=_EMPTY_KEYBOARD
            )
            if ephemeral:
                schedule_self_destruct(chat_id, message_id, source_id)
            chunks = chunks[1:]
        except Exception:
            logger.warning("Bot edit failed; sending a new message", exc_info=True)
    for chunk in chunks:
        sent_id = await telegram_service.send_message(chunk, chat_id=chat_id)
        if ephemeral and sent_id is not None:
            schedule_self_destruct(chat_id, sent_id, source_id)


async def _process_callback(callback: dict) -> None:
    sender = str((callback.get("from") or {}).get("id") or "")
    message = callback.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")
    data = callback.get("data") or ""
    await telegram_service.answer_callback_query(callback.get("id") or "")
    if chat_id is None or not _callback_allowed(message.get("chat") or message, sender, data):
        return
    parts = data.split(":")
    prefix = parts[0] if parts else ""
    raw_id = parts[1] if len(parts) > 1 else ""
    source_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
    if prefix == "cancel":
        if raw_id.isdigit():
            source_id = int(raw_id)
        for mid in _message_ids(message_id, source_id):
            _cancel_self_destruct(chat_id, mid)
            await telegram_service.delete_message(chat_id, mid)
        return
    if prefix == "live" and raw_id:
        try:
            async with SessionLocal() as session:
                accounts = await AccountRepository(session).list_all()
            found = _group_by_token(_live_groups(accounts), raw_id)
            text = (
                _live_card(*found)
                if found
                else f"That person is no longer in the list.{_live_ttl_note()}"
            )
            await _replace_message(chat_id, message_id, text, ephemeral=True, source_id=source_id)
        except Exception:
            logger.warning("Bot live callback failed: %s", data, exc_info=True)
        return
    if not _chat_allowed(message.get("chat") or message, sender):
        return
    if prefix == "who" and raw_id:
        try:
            async with SessionLocal() as session:
                accounts = await AccountRepository(session).list_all()
            found = _group_by_token(_people_groups(accounts), raw_id)
            text = _person_card(*found) if found else "That person is no longer in the list."
            await _replace_message(chat_id, message_id, text)
        except Exception:
            logger.warning("Bot people callback failed: %s", data, exc_info=True)
        return
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
                    if not account:
                        text = "That account no longer exists."
                    elif result.get("status") != "ok":
                        errors = "; ".join(f"{k}: {v}" for k, v in (result.get("errors") or {}).items())
                        text = f"⚠️ <b>Partial gateway update</b>\n<i>{html.escape(errors)}</i>\n\n{_account_card(account)}"
                    else:
                        headline = "🟢 <b>Gateway Enabled</b>" if enable else "⛔ <b>Gateway Disabled</b>"
                        text = f"{headline}\n\n{_account_card(account)}"
                except NewApiError as exc:
                    text = f"❌ Could not update the gateway: {html.escape(str(exc))}"
        await _replace_message(chat_id, message_id, text)
    except Exception:
        logger.warning("Bot callback failed: %s", data, exc_info=True)


_poll_state: dict = {
    "running": False,
    "last_ok_at": None,
    "last_error": None,
    "last_count": 0,
    "last_command": None,
}


def poller_snapshot() -> dict:
    snap = dict(_poll_state)
    snap["mode"] = "webhook"
    return snap


def _incoming_message(update: dict) -> dict:
    return (
        update.get("message")
        or update.get("edited_message")
        or update.get("business_message")
        or {}
    )


def _note_update_message_id(update: dict) -> None:
    callback = update.get("callback_query") or {}
    message = callback.get("message") or _incoming_message(update)
    chat = message.get("chat") or {}
    note = getattr(telegram_service, "note_group_message_id", None)
    if note is None:
        return
    note(chat.get("id"), message.get("message_id"))


async def _process_update(update: dict) -> None:
    try:
        _note_update_message_id(update)
        _poll_state["last_ok_at"] = datetime.now(timezone.utc).isoformat()
        _poll_state["last_count"] = int(_poll_state.get("last_count") or 0) + 1
        if update.get("callback_query"):
            await _process_callback(update["callback_query"])
            return
        message = _incoming_message(update)
        text = message.get("text") or ""
        if not text.startswith("/"):
            return
        sender = str((message.get("from") or {}).get("id") or "")
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            return
        command = text.strip().split(maxsplit=1)[0].split("@")[0].lower()
        thread_id = message.get("message_thread_id")
        _poll_state["last_command"] = f"{command} chat={chat_id} type={chat.get('type')}"
        if not _command_allowed(chat, sender, command):
            logger.info(
                "Ignoring bot command %s from %s in %s %s",
                command,
                sender,
                chat.get("type") or "unknown",
                chat_id,
            )
            return
        source_id = message.get("message_id")
        reply, keyboard = await _handle_command(
            text,
            public=not _chat_allowed(chat, sender),
            source_message_id=source_id,
        )
        messages = _split_telegram(reply) if isinstance(reply, str) else [reply]
        ephemeral = command == "/live"
        sent_ids: list[int] = []
        for index, message_text in enumerate(messages):
            sent_id = await telegram_service.send_message(
                message_text,
                chat_id=chat_id,
                reply_markup=keyboard if index == 0 else None,
                message_thread_id=thread_id,
            )
            if ephemeral and sent_id is not None:
                sent_ids.append(sent_id)
        if ephemeral:
            schedule_self_destruct(chat_id, *sent_ids, source_id)
    except Exception:
        logger.warning("Telegram update failed", exc_info=True)


WEBHOOK_PATH = "/api/telegram/webhook"
_WEBHOOK_CERT = Path("/run/secrets/telegram-webhook.crt")


def webhook_secret() -> str:
    settings = get_settings()
    raw = (settings.telegram_webhook_secret or "").strip()
    if raw:
        return raw
    return hashlib.sha256((settings.jwt_secret or "bot").encode()).hexdigest()[:48]


def webhook_public_url() -> str:
    raw = (get_settings().telegram_webhook_url or "").strip().rstrip("/")
    if not raw:
        return ""
    if raw.endswith(WEBHOOK_PATH):
        return raw
    return raw + WEBHOOK_PATH


def webhook_secret_ok(header: str | None) -> bool:
    expected = webhook_secret()
    got = header or ""
    if not expected or len(got) != len(expected):
        return False
    return hmac.compare_digest(got, expected)


async def setup_telegram_webhook() -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        _poll_state["running"] = False
        _poll_state["last_error"] = "token not set"
        logger.warning("Telegram bot token not set; bot commands disabled")
        return
    url = webhook_public_url()
    if not url:
        _poll_state["running"] = False
        _poll_state["last_error"] = "TELEGRAM_WEBHOOK_URL is not set"
        logger.warning("TELEGRAM_WEBHOOK_URL is not set; bot commands disabled")
        return
    body = {
        "url": url,
        "secret_token": webhook_secret(),
        "allowed_updates": ["message", "callback_query"],
        "drop_pending_updates": False,
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            if _WEBHOOK_CERT.is_file():
                response = await client.post(
                    f"https://api.telegram.org/bot{settings.telegram_bot_token}/setWebhook",
                    data={
                        "url": url,
                        "secret_token": webhook_secret(),
                        "allowed_updates": '["message","callback_query"]',
                        "drop_pending_updates": "false",
                    },
                    files={"certificate": (_WEBHOOK_CERT.name, _WEBHOOK_CERT.read_bytes(), "application/x-pem-file")},
                )
            else:
                response = await client.post(
                    f"https://api.telegram.org/bot{settings.telegram_bot_token}/setWebhook",
                    json=body,
                )
        data = response.json() if response.content else {}
        if response.status_code != 200 or not data.get("ok"):
            desc = str(data.get("description") or response.text or "setWebhook failed")
            _poll_state["running"] = False
            _poll_state["last_error"] = desc[:180]
            logger.warning("Telegram setWebhook failed: %s", _poll_state["last_error"])
            return
        _poll_state["running"] = True
        _poll_state["last_error"] = None
        _poll_state["last_ok_at"] = datetime.now(timezone.utc).isoformat()
        logger.info("Telegram webhook registered")
    except Exception as exc:
        _poll_state["running"] = False
        _poll_state["last_error"] = str(exc)[:180]
        logger.warning("Telegram setWebhook failed", exc_info=True)
