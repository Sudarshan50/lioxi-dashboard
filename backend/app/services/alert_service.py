"""Telegram alerts driven by NewAPI lifetime spend vs the Azure credit grant.

Azure's remaining-balance figure lags, so the meter is NewAPI O1+O2 spend.
Thresholds (default 75% and 95%) are spend / grant. Auto-stop fires when
spend reaches grant + a configurable overspend buffer (default $250).

Each threshold fires once per account, tracked in new_api_alert_level with a
re-arm margin. Level 100 marks the auto-stop announcement. Disable retries
on every sync until all mapped portals are actually off.
"""

import asyncio
import html
import json
import logging

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.app_setting import AppSetting
from app.repositories.account_repository import AccountRepository
from app.services import telegram_service
from app.services.account_service import AccountNotFoundError
from app.services.telegram_service import format_ist

logger = logging.getLogger(__name__)

ALERT_CONFIG_KEY = "alert_config"
DEFAULT_THRESHOLDS = [75, 95]
DEFAULT_REARM_MARGIN = 5.0
DEFAULT_OVERSPEND_BUFFER = 250.0
DEFAULT_SYNC_INTERVAL_MINUTES = 5
DEFAULT_AZURE_SYNC_INTERVAL_MINUTES = 30
MIN_SYNC_INTERVAL_MINUTES = 1
MIN_AZURE_SYNC_INTERVAL_MINUTES = 5
MAX_SYNC_INTERVAL_MINUTES = 180
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


def _sanitize_buffer(raw) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_OVERSPEND_BUFFER
    if value < 0 or value > 10_000:
        return DEFAULT_OVERSPEND_BUFFER
    return value


def _sanitize_sync_interval(
    raw, default: int, minimum: int = MIN_SYNC_INTERVAL_MINUTES, maximum: int = MAX_SYNC_INTERVAL_MINUTES
) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    if value < minimum or value > maximum:
        return default
    return value


async def get_alert_config(session: AsyncSession) -> dict:
    config = {
        "enabled": True,
        "thresholds": list(DEFAULT_THRESHOLDS),
        "rearm_margin": DEFAULT_REARM_MARGIN,
        "overspend_buffer_usd": DEFAULT_OVERSPEND_BUFFER,
        "sync_interval_minutes": DEFAULT_SYNC_INTERVAL_MINUTES,
        "azure_sync_interval_minutes": DEFAULT_AZURE_SYNC_INTERVAL_MINUTES,
    }
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
    config["overspend_buffer_usd"] = _sanitize_buffer(stored.get("overspend_buffer_usd", DEFAULT_OVERSPEND_BUFFER))
    config["sync_interval_minutes"] = _sanitize_sync_interval(
        stored.get("sync_interval_minutes", DEFAULT_SYNC_INTERVAL_MINUTES),
        DEFAULT_SYNC_INTERVAL_MINUTES,
    )
    config["azure_sync_interval_minutes"] = _sanitize_sync_interval(
        stored.get("azure_sync_interval_minutes", DEFAULT_AZURE_SYNC_INTERVAL_MINUTES),
        DEFAULT_AZURE_SYNC_INTERVAL_MINUTES,
        MIN_AZURE_SYNC_INTERVAL_MINUTES,
    )
    if (
        raw_thresholds != config["thresholds"]
        or "overspend_buffer_usd" not in stored
        or "sync_interval_minutes" not in stored
        or "azure_sync_interval_minutes" not in stored
    ):
        row.value = json.dumps(config)
        await session.commit()
    return config


def normalize_alert_config(payload: dict) -> dict:
    thresholds = sorted({int(t) for t in (payload.get("thresholds") or [])})
    if not thresholds:
        raise ValueError("At least one alert threshold is required")
    if any(t < 1 or t > 99 for t in thresholds):
        raise ValueError("Thresholds must be between 1 and 99 percent (auto-disable uses the grant + buffer)")
    rearm_margin = float(payload.get("rearm_margin", DEFAULT_REARM_MARGIN))
    if rearm_margin < 0 or rearm_margin > 100:
        raise ValueError("Re-arm margin must be between 0 and 100 points")
    buffer = float(payload.get("overspend_buffer_usd", DEFAULT_OVERSPEND_BUFFER))
    if buffer < 0 or buffer > 10_000:
        raise ValueError("Overspend buffer must be between $0 and $10,000")
    interval = int(payload.get("sync_interval_minutes", DEFAULT_SYNC_INTERVAL_MINUTES))
    if interval < MIN_SYNC_INTERVAL_MINUTES or interval > MAX_SYNC_INTERVAL_MINUTES:
        raise ValueError(
            f"NewAPI sync interval must be between {MIN_SYNC_INTERVAL_MINUTES} and {MAX_SYNC_INTERVAL_MINUTES} minutes"
        )
    azure_interval = int(payload.get("azure_sync_interval_minutes", DEFAULT_AZURE_SYNC_INTERVAL_MINUTES))
    if azure_interval < MIN_AZURE_SYNC_INTERVAL_MINUTES or azure_interval > MAX_SYNC_INTERVAL_MINUTES:
        raise ValueError(
            f"Azure sync interval must be between {MIN_AZURE_SYNC_INTERVAL_MINUTES} and {MAX_SYNC_INTERVAL_MINUTES} minutes"
        )
    return {
        "enabled": bool(payload.get("enabled", True)),
        "thresholds": thresholds,
        "rearm_margin": rearm_margin,
        "overspend_buffer_usd": buffer,
        "sync_interval_minutes": interval,
        "azure_sync_interval_minutes": azure_interval,
    }


async def save_alert_config(session: AsyncSession, payload: dict) -> dict:
    config = normalize_alert_config(payload)
    row = await session.get(AppSetting, ALERT_CONFIG_KEY)
    if row is None:
        session.add(AppSetting(key=ALERT_CONFIG_KEY, value=json.dumps(config)))
    else:
        row.value = json.dumps(config)
    await session.commit()
    return config


def new_api_spend(account) -> float | None:
    if account.new_api_cost_usd is None:
        return None
    return max(float(account.new_api_cost_usd), 0.0)


def _nominal_grant_usd(limit: float) -> float:
    """Azure lot totals sometimes include a small add-on on top of the real
    package (e.g. $10,000 + $200 promo = $10,200). Stop-at and % alerts use
    the intended grant so those accounts do not get extra headroom.
    """
    if 10_000 < limit <= 10_300:
        return 10_000.0
    return limit


def credit_grant_usd(account) -> float | None:
    """Azure credit grant in USD. NewAPI spend is always USD, so INR grants
    are converted with the configured FX rate.
    """
    limit = account.credits_limit
    if limit is None or limit <= 0:
        return None
    code = (account.credits_currency or "USD").upper()
    if code == "INR":
        rate = get_settings().usd_inr_rate or 87
        if rate <= 0:
            return None
        limit = limit / rate
    return _nominal_grant_usd(float(limit))


def stop_at_usd(account, overspend_buffer: float = DEFAULT_OVERSPEND_BUFFER) -> float | None:
    grant = credit_grant_usd(account)
    if grant is None:
        return None
    return grant + max(overspend_buffer, 0.0)


def consumed_percent(account) -> float | None:
    """NewAPI lifetime spend as a percent of the Azure credit grant."""
    spend = new_api_spend(account)
    grant = credit_grant_usd(account)
    if spend is None or grant is None:
        return None
    return spend / grant * 100


def credits_exhausted(account, overspend_buffer: float = DEFAULT_OVERSPEND_BUFFER) -> bool:
    """Stop when NewAPI spend reaches grant + buffer."""
    spend = new_api_spend(account)
    cap = stop_at_usd(account, overspend_buffer)
    if spend is None or cap is None:
        return False
    return spend >= cap


def exhausted_reason(account, overspend_buffer: float = DEFAULT_OVERSPEND_BUFFER) -> str | None:
    if not credits_exhausted(account, overspend_buffer):
        return None
    return "overspent"


def _crossed_level(percent: float, thresholds: list[int]) -> int:
    level = 0
    for threshold in sorted(thresholds):
        if percent >= threshold:
            level = threshold
    return level


def _level_title(level: int) -> str:
    if level >= 95:
        return "Nearly exhausted"
    if level >= 75:
        return "Running hot"
    return "Heads up"


def _money(value: float | None, currency: str | None = "USD") -> str:
    if value is None:
        return "—"
    code = (currency or "USD").upper()
    if code == "INR":
        return f"₹{value:,.2f}"
    if code == "USD":
        return f"${value:,.2f}"
    return f"{value:,.2f} {code}"


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
    grant = credit_grant_usd(account)
    return (
        f"<b>{_level_title(level)} — {level}% of credit grant spent (NewAPI)</b>\n"
        f"Account   <b>{html.escape(account.name)}</b>\n"
        f"Channel   {html.escape(account.new_api_name or '—')} · {account.new_api_gateway or '—'}\n"
        f"NewAPI    <b>{_spend_line(account)}</b> of {_money(grant)}\n"
        f"Consumed  <b>{percent:.1f}%</b>\n"
        f"{format_ist()}"
    )


def _format_exhausted_alert(
    account,
    flipped: dict | None,
    flip_error: str | None,
    still_live: bool = False,
    overspend_buffer: float = DEFAULT_OVERSPEND_BUFFER,
) -> str:
    parts: list[str] = []
    if flipped:
        parts.append("Auto-disabled  " + " · ".join(sorted(flipped)))
    if still_live and flip_error:
        parts.append(f"Still live on some portals — will retry next sync\n<i>{html.escape(flip_error)}</i>")
    if not parts:
        parts.append("Channels were already disabled")
    action = "\n".join(parts)
    grant = credit_grant_usd(account)
    cap = stop_at_usd(account, overspend_buffer)
    return (
        f"<b>NewAPI spend hit grant + buffer — gateway stopped</b>\n"
        f"Account   <b>{html.escape(account.name)}</b>\n"
        f"Channel   {html.escape(account.new_api_name or '—')} · {account.new_api_gateway or '—'}\n"
        f"NewAPI    <b>{_spend_line(account)}</b>\n"
        f"Grant     {_money(grant)}\n"
        f"Stop at   {_money(cap)}  <i>(grant + ${overspend_buffer:,.0f})</i>\n"
        f"{action}\n"
        f"{format_ist()}"
    )


async def _send_spaced(text: str, sent_so_far: int) -> None:
    # Telegram allows ~20 messages/min per group; space out bursts.
    if sent_so_far:
        await asyncio.sleep(4)
    await telegram_service.send_message(text)


async def alert_state(session: AsyncSession) -> list[dict]:
    """Per-account alert snapshot for the portal UI."""
    config = await get_alert_config(session)
    buffer = float(config["overspend_buffer_usd"])
    accounts = await AccountRepository(session).list_all()
    items = []
    for account in accounts:
        spend = new_api_spend(account)
        grant = credit_grant_usd(account)
        cap = stop_at_usd(account, buffer)
        headroom = None if spend is None or cap is None else cap - spend
        auto_cap = credits_exhausted(account, buffer)
        manual_cap = bool(getattr(account, "at_cap_manual", False))
        items.append(
            {
                "id": account.id,
                "name": account.name,
                "new_api_name": account.new_api_name or "",
                "new_api_tag": account.new_api_tag or "",
                "owner_tag": account.owner_tag or "",
                "gateway": account.new_api_gateway,
                "gateway_enabled": account.new_api_status == 1,
                "spend_usd": spend or 0,
                "spend_o1_usd": account.new_api_cost_o1_usd,
                "spend_o2_usd": account.new_api_cost_o2_usd,
                "endpoint": account.endpoint or "",
                "credits_limit": grant,
                "credits_currency": "USD",
                "stop_at_usd": cap,
                "headroom_usd": headroom,
                "overspend_buffer_usd": buffer,
                "percent": consumed_percent(account),
                "exhausted": auto_cap or manual_cap,
                "exhausted_reason": "overspent" if auto_cap else "manual" if manual_cap else None,
                "at_cap_manual": manual_cap,
                "alert_level": account.new_api_alert_level or 0,
                "payable_settled": bool(getattr(account, "payable_settled", False)),
                "payable_settled_at": account.payable_settled_at.isoformat()
                if getattr(account, "payable_settled_at", None)
                else None,
            }
        )
    items.sort(
        key=lambda item: (
            0 if item["gateway_enabled"] else 1,
            -(item["percent"] if item["percent"] is not None else -1),
        )
    )
    return items


async def set_payable_settled(session: AsyncSession, account_id: int, settled: bool) -> dict:
    """Mark an account's payable as paid/settled. Does not change gateway status."""
    account = await AccountRepository(session).get(account_id)
    if account is None:
        raise AccountNotFoundError("Account not found.")
    account.payable_settled = bool(settled)
    account.payable_settled_at = datetime.now(timezone.utc) if settled else None
    await AccountRepository(session).save(account)
    return {
        "id": account.id,
        "payable_settled": account.payable_settled,
        "payable_settled_at": account.payable_settled_at.isoformat() if account.payable_settled_at else None,
    }


async def set_at_cap_manual(session: AsyncSession, account_id: int, tagged: bool) -> dict:
    """Manually tag a disabled account as at-cap so it counts in unsettled payable."""
    account = await AccountRepository(session).get(account_id)
    if account is None:
        raise AccountNotFoundError("Account not found.")
    if tagged and account.new_api_status == 1:
        raise ValueError("Only disabled accounts can be tagged at cap.")
    account.at_cap_manual = bool(tagged)
    await AccountRepository(session).save(account)
    return {"id": account.id, "at_cap_manual": account.at_cap_manual}


async def check_new_api_credit_alerts(session: AsyncSession) -> dict:
    """Threshold alerts on NewAPI spend / grant, plus auto-disable when
    spend reaches grant + overspend buffer.

    Auto-disable always runs. Telegram is optional — pausing alerts or a
    missing bot token must not leave exhausted channels routing.
    """
    from app.services.new_api_service import gateway_still_live, set_gateway_status

    config = await get_alert_config(session)
    notify = telegram_service.is_configured() and bool(config["enabled"])
    thresholds: list[int] = config["thresholds"]
    rearm_margin: float = config["rearm_margin"]
    buffer: float = float(config["overspend_buffer_usd"])

    accounts = await AccountRepository(session).list_all()
    sent = 0
    auto_disabled: list[str] = []
    for account in accounts:
        if account.new_api_gateway is None:
            continue
        previous = min(account.new_api_alert_level or 0, EXHAUSTED_LEVEL)
        live = gateway_still_live(account)

        if credits_exhausted(account, buffer):
            flipped: dict | None = None
            flip_error: str | None = None
            if live:
                try:
                    result = await set_gateway_status(session, account.id, 2)
                    await session.refresh(account)
                    flipped = result.get("flipped") or None
                    if result.get("errors") and gateway_still_live(account):
                        flip_error = "; ".join(result["errors"].values())[:150]
                    if flipped:
                        auto_disabled.append(account.name)
                except Exception as exc:  # noqa: BLE001 - keep checking other accounts
                    flip_error = str(exc)[:150]
                    logger.warning("Auto-disable failed for %s", account.name, exc_info=True)
            still_live = gateway_still_live(account)
            # Telegram only when we actually flipped a live channel (or the flip failed).
            # Already-disabled accounts stay quiet.
            should_announce = (
                notify
                and live
                and previous < EXHAUSTED_LEVEL
                and (bool(flipped) or flip_error is not None)
            )
            if should_announce:
                try:
                    await _send_spaced(
                        _format_exhausted_alert(account, flipped, flip_error, still_live, buffer),
                        sent,
                    )
                    sent += 1
                    account.new_api_alert_level = EXHAUSTED_LEVEL
                except Exception:
                    logger.warning("Telegram exhausted alert failed for %s", account.name, exc_info=True)
            elif not still_live and previous < EXHAUSTED_LEVEL:
                account.new_api_alert_level = EXHAUSTED_LEVEL
            continue

        if not live:
            continue

        percent = consumed_percent(account)
        if percent is None:
            continue
        level = _crossed_level(percent, thresholds)

        if previous >= EXHAUSTED_LEVEL:
            account.new_api_alert_level = level
            continue

        if not notify:
            continue

        if level > previous:
            try:
                await _send_spaced(_format_threshold_alert(account, level, percent), sent)
                sent += 1
                account.new_api_alert_level = level
            except Exception:
                logger.warning("Telegram alert failed for %s", account.name, exc_info=True)
        elif level < previous and percent < previous - rearm_margin:
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
