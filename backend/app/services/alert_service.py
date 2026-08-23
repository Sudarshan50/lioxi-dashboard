"""Telegram alerts driven by Azure credit consumption.

Basis: percent = (credits_limit - credits_remaining) / credits_limit, straight
from Azure's own credit figures. Thresholds (default 75% and 95%) fire once
per account, tracked in new_api_alert_level with a re-arm margin so jitter
never duplicates an alert.

Auto-stop fires when Azure remaining credits are at (or below) zero, or when
outstanding/pending charges already meet or exceed the Azure credit grant
(the invoice-lag case where remaining can still look positive). Channels are
disabled on every matched portal and the group is told — level 100 marks
that announcement. The disable retries on every sync until all channels
are actually off.

Thresholds, the re-arm margin, and a global on/off switch are configurable
from the portal and persisted in the app_settings table.
"""

import asyncio
import html
import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_setting import AppSetting
from app.repositories.account_repository import AccountRepository
from app.services import telegram_service
from app.services.telegram_service import format_ist

logger = logging.getLogger(__name__)

ALERT_CONFIG_KEY = "alert_config"
DEFAULT_THRESHOLDS = [75, 95]
DEFAULT_REARM_MARGIN = 5.0
EXHAUSTED_LEVEL = 100


def _sanitize_thresholds(raw) -> list[int]:
    values: list[int] = []
    for value in raw or []:
        try:
            values.append(int(value))
        except (TypeError, ValueError):
            continue
    # One-shot migrate the original 50/75/100 rule set to 75/95.
    if set(values) == {50, 75, 100}:
        return list(DEFAULT_THRESHOLDS)
    cleaned = sorted({number for number in values if 1 <= number <= 99})
    return cleaned or list(DEFAULT_THRESHOLDS)


async def get_alert_config(session: AsyncSession) -> dict:
    config = {"enabled": True, "thresholds": list(DEFAULT_THRESHOLDS), "rearm_margin": DEFAULT_REARM_MARGIN}
    row = await session.get(AppSetting, ALERT_CONFIG_KEY)
    if row is None:
        return config
    try:
        stored = json.loads(row.value)
    except (ValueError, TypeError):
        logger.warning("Stored alert config is corrupt; using defaults")
        return config
    config["enabled"] = bool(stored.get("enabled", True))
    raw_thresholds = stored.get("thresholds", config["thresholds"])
    config["thresholds"] = _sanitize_thresholds(raw_thresholds)
    try:
        config["rearm_margin"] = float(stored.get("rearm_margin", DEFAULT_REARM_MARGIN))
    except (TypeError, ValueError):
        config["rearm_margin"] = DEFAULT_REARM_MARGIN
    if raw_thresholds != config["thresholds"]:
        row.value = json.dumps(config)
        await session.commit()
    return config


def normalize_alert_config(payload: dict) -> dict:
    thresholds = sorted({int(t) for t in (payload.get("thresholds") or [])})
    if not thresholds:
        raise ValueError("At least one alert threshold is required")
    if any(t < 1 or t > 99 for t in thresholds):
        raise ValueError("Thresholds must be between 1 and 99 percent (100% auto-disables)")
    rearm_margin = float(payload.get("rearm_margin", DEFAULT_REARM_MARGIN))
    if rearm_margin < 0 or rearm_margin > 100:
        raise ValueError("Re-arm margin must be between 0 and 100 points")
    return {"enabled": bool(payload.get("enabled", True)), "thresholds": thresholds, "rearm_margin": rearm_margin}


async def save_alert_config(session: AsyncSession, payload: dict) -> dict:
    config = normalize_alert_config(payload)
    row = await session.get(AppSetting, ALERT_CONFIG_KEY)
    if row is None:
        session.add(AppSetting(key=ALERT_CONFIG_KEY, value=json.dumps(config)))
    else:
        row.value = json.dumps(config)
    await session.commit()
    return config


def credit_outstanding(account) -> float | None:
    """Same outstanding figure the Accounts page shows: Azure pending/used,
    falling back to limit − remaining when Azure omitted used.
    """
    if not account.credits_available:
        return None
    if account.credits_used is not None:
        return max(account.credits_used, 0.0)
    if account.credits_limit is not None and account.credits_remaining is not None:
        return max(account.credits_limit - account.credits_remaining, 0.0)
    return None


def consumed_percent(account) -> float | None:
    """Percent of the Azure credit grant consumed, from Azure's own numbers.
    None when Azure did not report a usable limit/remaining pair.
    """
    if not account.credits_available:
        return None
    limit = account.credits_limit
    remaining = account.credits_remaining
    if limit is None or limit <= 0 or remaining is None:
        return None
    consumed = max(limit - remaining, 0.0)
    return min(consumed / limit * 100, 100.0)


def credits_exhausted(account) -> bool:
    """Stop when the leftover balance is gone, or outstanding already
    consumes the whole grant (remaining can lag behind pending charges).
    """
    if not account.credits_available:
        return False
    remaining = account.credits_remaining
    limit = account.credits_limit
    outstanding = credit_outstanding(account)
    if remaining is not None and remaining <= 0:
        return True
    return outstanding is not None and limit is not None and outstanding >= limit


def exhausted_reason(account) -> str | None:
    if not credits_exhausted(account):
        return None
    remaining = account.credits_remaining
    if remaining is not None and remaining <= 0:
        return "zero"
    return "overspent"


def _crossed_level(percent: float, thresholds: list[int]) -> int:
    level = 0
    for threshold in sorted(thresholds):
        if percent >= threshold:
            level = threshold
    return level


def _level_icon(level: int) -> str:
    if level >= 95:
        return "🚨"
    if level >= 75:
        return "🟠"
    return "🔔"


def _level_title(level: int) -> str:
    if level >= 95:
        return "Nearly Exhausted"
    if level >= 75:
        return "Running Hot"
    return "Heads Up"


def _bar(percent: float, slots: int = 12) -> str:
    filled = min(slots, round(percent / 100 * slots))
    return "█" * filled + "░" * (slots - filled)


def _money(value: float | None, currency: str | None = "USD") -> str:
    if value is None:
        return "—"
    code = (currency or "USD").upper()
    if code == "INR":
        return f"₹{value:,.2f}"
    if code == "USD":
        return f"${value:,.2f}"
    return f"{value:,.2f} {code}"


def _credit_money(account, value: float | None) -> str:
    return _money(value, account.credits_currency)


def _spend_line(account) -> str:
    labels = (account.new_api_gateway or "").split("+")
    parts = []
    if "O1" in labels:
        parts.append(f"O1 {_money(account.new_api_cost_o1_usd or 0)}")
    if "O2" in labels:
        parts.append(f"O2 {_money(account.new_api_cost_o2_usd or 0)}")
    split = f" ({' · '.join(parts)})" if len(parts) > 1 else ""
    return f"{_money(account.new_api_cost_usd or 0)}{split}"


def _format_threshold_alert(account, level: int, percent: float) -> str:
    consumed = max((account.credits_limit or 0) - (account.credits_remaining or 0), 0.0)
    return (
        f"{_level_icon(level)} <b>{_level_title(level)} — {level}% of Azure credits consumed</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏦 Account     <b>{html.escape(account.name)}</b>\n"
        f"📡 Channel     {html.escape(account.new_api_name or '—')} · {account.new_api_gateway or '—'}\n"
        f"💳 Consumed    <b>{_credit_money(account, consumed)}</b> of {_credit_money(account, account.credits_limit)}\n"
        f"💰 Remaining   {_credit_money(account, account.credits_remaining)}\n"
        f"💸 NewAPI      {_spend_line(account)}\n"
        f"📊 {_bar(percent)}  <b>{percent:.1f}%</b>\n"
        f"🕒 {format_ist()}"
    )


def _format_exhausted_alert(account, flipped: dict | None, flip_error: str | None) -> str:
    parts: list[str] = []
    if flipped:
        parts.append("🔌 Auto-disabled  " + " · ".join(f"{label} ✓" for label in sorted(flipped)))
    if flip_error:
        parts.append(f"⚠️ Still live on some portals — will retry next sync\n<i>{html.escape(flip_error)}</i>")
    if not parts:
        parts.append("🔌 Channels were already disabled")
    action = "\n".join(parts)
    reason = exhausted_reason(account)
    why = (
        "Outstanding charges already exceed the Azure credit grant"
        if reason == "overspent"
        else "Azure remaining credits hit zero"
    )
    return (
        f"⛔ <b>Credits Exhausted — Gateway Stopped</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏦 Account     <b>{html.escape(account.name)}</b>\n"
        f"📡 Channel     {html.escape(account.new_api_name or '—')} · {account.new_api_gateway or '—'}\n"
        f"💰 Remaining   <b>{_credit_money(account, account.credits_remaining)}</b> of {_credit_money(account, account.credits_limit)}\n"
        f"📄 Outstanding {_credit_money(account, credit_outstanding(account))}\n"
        f"💸 NewAPI      {_spend_line(account)}\n"
        f"📌 {why}\n"
        f"{action}\n"
        f"🕒 {format_ist()}"
    )


async def _send_spaced(text: str, sent_so_far: int) -> None:
    # Telegram allows ~20 messages/min per group; space out bursts.
    if sent_so_far:
        await asyncio.sleep(4)
    await telegram_service.send_message(text)


async def alert_state(session: AsyncSession) -> list[dict]:
    """Per-account alert snapshot for the portal UI."""
    accounts = await AccountRepository(session).list_all()
    items = []
    for account in accounts:
        if account.new_api_gateway is None and account.new_api_cost_usd is None:
            continue
        items.append(
            {
                "id": account.id,
                "name": account.name,
                "gateway": account.new_api_gateway,
                "gateway_enabled": account.new_api_status == 1,
                "spend_usd": account.new_api_cost_usd or 0,
                "credits_remaining": account.credits_remaining,
                "credits_limit": account.credits_limit,
                "credits_outstanding": credit_outstanding(account),
                "credits_currency": account.credits_currency or "USD",
                "percent": consumed_percent(account),
                "exhausted": credits_exhausted(account),
                "exhausted_reason": exhausted_reason(account),
                "alert_level": account.new_api_alert_level or 0,
            }
        )
    items.sort(key=lambda item: item["percent"] if item["percent"] is not None else -1, reverse=True)
    return items


async def check_new_api_credit_alerts(session: AsyncSession) -> dict:
    """Threshold alerts on Azure credit consumption plus auto-disable when
    remaining hits zero or outstanding meets the grant.

    Auto-disable always runs. Telegram is optional — pausing alerts or a
    missing bot token must not leave exhausted channels routing.
    """
    from app.services.new_api_service import gateway_still_live, set_gateway_status

    config = await get_alert_config(session)
    notify = telegram_service.is_configured() and bool(config["enabled"])
    thresholds: list[int] = config["thresholds"]
    rearm_margin: float = config["rearm_margin"]

    accounts = await AccountRepository(session).list_all()
    sent = 0
    auto_disabled: list[str] = []
    for account in accounts:
        if account.new_api_gateway is None:
            continue
        previous = min(account.new_api_alert_level or 0, EXHAUSTED_LEVEL)

        if credits_exhausted(account):
            flipped: dict | None = None
            flip_error: str | None = None
            if gateway_still_live(account):
                try:
                    result = await set_gateway_status(session, account.id, 2)
                    await session.refresh(account)
                    flipped = result.get("flipped") or None
                    if result.get("errors"):
                        flip_error = "; ".join(result["errors"].values())[:150]
                    if flipped and not result.get("errors"):
                        auto_disabled.append(account.name)
                    elif flipped:
                        auto_disabled.append(account.name)
                except Exception as exc:  # noqa: BLE001 - keep checking other accounts
                    flip_error = str(exc)[:150]
                    logger.warning("Auto-disable failed for %s", account.name, exc_info=True)
            still_live = gateway_still_live(account)
            should_announce = notify and previous < EXHAUSTED_LEVEL and (bool(flipped) or flip_error is not None)
            if should_announce:
                try:
                    await _send_spaced(_format_exhausted_alert(account, flipped, flip_error), sent)
                    sent += 1
                    account.new_api_alert_level = EXHAUSTED_LEVEL
                except Exception:
                    logger.warning("Telegram exhausted alert failed for %s", account.name, exc_info=True)
            elif not still_live and previous < EXHAUSTED_LEVEL:
                account.new_api_alert_level = EXHAUSTED_LEVEL
            continue

        if not notify:
            continue
        percent = consumed_percent(account)
        if percent is None:
            continue
        level = _crossed_level(percent, thresholds)

        if level > previous:
            if account.new_api_status != 1:
                continue
            try:
                await _send_spaced(_format_threshold_alert(account, level, percent), sent)
                sent += 1
                account.new_api_alert_level = level
            except Exception:
                logger.warning("Telegram alert failed for %s", account.name, exc_info=True)
        elif previous < EXHAUSTED_LEVEL and level < previous and percent < previous - rearm_margin:
            account.new_api_alert_level = level

    await session.commit()
    if sent or auto_disabled:
        logger.info("Credit alerts sent: %d, auto-disabled: %s", sent, auto_disabled or "none")
    summary = {"sent": sent, "auto_disabled": auto_disabled}
    if not telegram_service.is_configured():
        summary["skipped"] = "telegram not configured (auto-disable still ran)"
    elif not config["enabled"]:
        summary["skipped"] = "alerts paused (auto-disable still ran)"
    return summary
