from fastapi import APIRouter, Header, HTTPException, Request

from app.services.telegram_bot import _process_update, webhook_secret_ok

router = APIRouter(prefix="/api/telegram", tags=["telegram"])


@router.post("/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    if not webhook_secret_ok(x_telegram_bot_api_secret_token):
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        update = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid update") from exc
    if not isinstance(update, dict):
        raise HTTPException(status_code=400, detail="Invalid update")
    await _process_update(update)
    return {"ok": True}
