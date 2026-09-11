import html

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.dependencies import get_current_admin
from app.services.alert_service import (
    alert_state,
    check_new_api_credit_alerts,
    get_alert_config,
    save_alert_config,
    set_at_cap_manual,
    set_payable_settled,
)
from app.services.account_service import AccountNotFoundError
from app.services.join_group import GROUP_SB, GROUPS, normalize_group
from app.services.sync_scheduler import apply_azure_sync_interval, apply_sync_interval
from app.services.telegram_bot import poller_snapshot, start_clear_group_chat
from app.services.telegram_service import (
    TelegramError,
    cancel_clear_group_chat,
    clear_group_snapshot,
    is_configured,
    send_message,
)

router = APIRouter(prefix="/api/alerts", tags=["alerts"], dependencies=[Depends(get_current_admin)])


class AlertConfigPayload(BaseModel):
    enabled: bool = True
    thresholds: list[int]
    rearm_margin: float = 5.0
    overspend_buffer_usd: float = 250.0
    sync_interval_minutes: int = 5
    azure_sync_interval_minutes: int = 30


@router.get("/status")
async def get_status(db: AsyncSession = Depends(get_db)):
    settings = get_settings()
    config = await get_alert_config(db)
    return {
        "telegram_configured": any(is_configured(group) for group in GROUPS),
        "chat_id_set": bool(settings.telegram_chat_id),
        "chat_ids_set": {group: is_configured(group) for group in GROUPS},
        "admin_count": len(settings.telegram_admin_id_set),
        "alerts_enabled": config["enabled"],
        "clear_chats": clear_group_snapshot(),
        "clear_chats_by_group": {group: clear_group_snapshot(group) for group in GROUPS},
        "bot_poller": poller_snapshot(),
    }


@router.get("/config")
async def read_config(db: AsyncSession = Depends(get_db)):
    return await get_alert_config(db)


@router.put("/config")
async def update_config(payload: AlertConfigPayload, db: AsyncSession = Depends(get_db)):
    try:
        config = await save_alert_config(db, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    apply_sync_interval(int(config["sync_interval_minutes"]))
    apply_azure_sync_interval(int(config["azure_sync_interval_minutes"]))
    return config


class PayableSettledPayload(BaseModel):
    settled: bool


class AtCapManualPayload(BaseModel):
    at_cap: bool


@router.get("/state")
async def read_state(db: AsyncSession = Depends(get_db)):
    return await alert_state(db)


@router.patch("/state/{account_id}/settled")
async def update_payable_settled(
    account_id: int, payload: PayableSettledPayload, db: AsyncSession = Depends(get_db)
):
    try:
        return await set_payable_settled(db, account_id, payload.settled)
    except AccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/state/{account_id}/at-cap")
async def update_at_cap_manual(
    account_id: int, payload: AtCapManualPayload, db: AsyncSession = Depends(get_db)
):
    try:
        return await set_at_cap_manual(db, account_id, payload.at_cap)
    except AccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class GroupMessagePayload(BaseModel):
    text: str = Field(min_length=1, max_length=3900)
    # "sb", "vcs" or "both". Defaults to SB, the historical destination.
    target: str = Field(default=GROUP_SB, max_length=8)


def _targets(raw: str | None) -> list[str]:
    """Explicit fan-out list. 'both' is the only way to reach two chats.

    An unrecognised target is rejected rather than defaulted: silently falling
    back to SB would deliver a message to a group the admin did not choose.
    """
    wanted = (raw or "").strip().lower()
    if wanted == "both":
        return list(GROUPS)
    if wanted not in GROUPS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown target {raw!r}. Use {', '.join(GROUPS)} or both.",
        )
    return [wanted]


async def _send_to_targets(text: str, raw_target: str | None) -> dict:
    groups = _targets(raw_target)
    sent: list[str] = []
    errors: dict[str, str] = {}
    for group in groups:
        try:
            await send_message(text, group=group)
            sent.append(group)
        except TelegramError as exc:
            errors[group] = str(exc)
    if not sent:
        raise HTTPException(status_code=400, detail="; ".join(errors.values()) or "Send failed.")
    return {"status": "sent", "sent": sent, "errors": errors}


@router.post("/message")
async def send_group_message(payload: GroupMessagePayload):
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Message is empty.")
    return await _send_to_targets(html.escape(text), payload.target)


@router.get("/clear-chats")
async def read_clear_group_chat(group: str = Query(default=GROUP_SB, max_length=8)):
    return clear_group_snapshot(group)


@router.post("/clear-chats")
async def start_clear_group(group: str = Query(default=GROUP_SB, max_length=8)):
    try:
        return await start_clear_group_chat(group)
    except TelegramError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/clear-chats")
async def stop_clear_group(group: str = Query(default=GROUP_SB, max_length=8)):
    return await cancel_clear_group_chat(group)


@router.post("/test")
async def send_test_alert(
    db: AsyncSession = Depends(get_db),
    target: str = Query(default=GROUP_SB, max_length=8),
):
    config = await get_alert_config(db)
    levels = " and ".join(f"<b>{t}%</b>" for t in config["thresholds"])
    buffer = config.get("overspend_buffer_usd", 250)
    sample = (
        f"<b>Portal connected</b>\n"
        f"Credit alerts are live. You'll be notified when any account's\n"
        f"NewAPI spend hits {levels} of its Azure credit grant. Channels are\n"
        f"<b>auto-disabled</b> when NewAPI spend reaches grant + ${buffer:,.0f}."
    )
    return await _send_to_targets(sample, target)


@router.post("/check")
async def run_alert_check(db: AsyncSession = Depends(get_db)):
    return await check_new_api_credit_alerts(db)
