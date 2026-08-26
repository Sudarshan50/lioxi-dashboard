from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
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
from app.services.sync_scheduler import apply_azure_sync_interval, apply_sync_interval
from app.services.telegram_service import TelegramError, is_configured, send_message

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
        "telegram_configured": is_configured(),
        "chat_id_set": bool(settings.telegram_chat_id),
        "admin_count": len(settings.telegram_admin_id_set),
        "alerts_enabled": config["enabled"],
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


@router.post("/test")
async def send_test_alert(db: AsyncSession = Depends(get_db)):
    config = await get_alert_config(db)
    levels = " and ".join(f"<b>{t}%</b>" for t in config["thresholds"])
    buffer = config.get("overspend_buffer_usd", 250)
    sample = (
        f"<b>Portal connected</b>\n"
        f"Credit alerts are live. You'll be notified when any account's\n"
        f"NewAPI spend hits {levels} of its Azure credit grant. Channels are\n"
        f"<b>auto-disabled</b> when NewAPI spend reaches grant + ${buffer:,.0f}."
    )
    try:
        await send_message(sample)
    except TelegramError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "sent"}


@router.post("/check")
async def run_alert_check(db: AsyncSession = Depends(get_db)):
    return await check_new_api_credit_alerts(db)
