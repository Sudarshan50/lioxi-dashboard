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
)
from app.services.telegram_service import TelegramError, is_configured, send_message

router = APIRouter(prefix="/api/alerts", tags=["alerts"], dependencies=[Depends(get_current_admin)])


class AlertConfigPayload(BaseModel):
    enabled: bool = True
    thresholds: list[int]
    rearm_margin: float = 5.0


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
        return await save_alert_config(db, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/state")
async def read_state(db: AsyncSession = Depends(get_db)):
    return await alert_state(db)


@router.post("/test")
async def send_test_alert(db: AsyncSession = Depends(get_db)):
    config = await get_alert_config(db)
    levels = " and ".join(f"<b>{t}%</b>" for t in config["thresholds"])
    sample = (
        "✅ <b>Portal Connected</b>\n"
        "────────────────────\n"
        f"Credit alerts are live. You'll be notified when any account\n"
        f"consumes {levels} of its Azure credits. Channels are\n"
        "<b>auto-disabled</b> when remaining hits zero, or when outstanding\n"
        "charges already consume the Azure credit grant."
    )
    try:
        await send_message(sample)
    except TelegramError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "sent"}


@router.post("/check")
async def run_alert_check(db: AsyncSession = Depends(get_db)):
    return await check_new_api_credit_alerts(db)
