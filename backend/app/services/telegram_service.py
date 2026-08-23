"""Thin client for Telegram bot notifications (group alerts)."""

import logging
from datetime import datetime, timedelta, timezone

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))


def format_ist(value: datetime | None = None, fmt: str = "%d %b %Y %H:%M") -> str:
    """Format a datetime in India Standard Time. Naive values are treated as UTC."""
    moment = value if value is not None else datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return f"{moment.astimezone(IST).strftime(fmt)} IST"


class TelegramError(RuntimeError):
    pass


def is_configured() -> bool:
    settings = get_settings()
    return bool(settings.telegram_bot_token and settings.telegram_chat_id)


async def send_message(text: str, chat_id: str | int | None = None, reply_markup: dict | None = None) -> None:
    settings = get_settings()
    if not settings.telegram_bot_token or not (chat_id or settings.telegram_chat_id):
        raise TelegramError("Telegram is not configured (set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)")
    payload: dict = {
        "chat_id": chat_id or settings.telegram_chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
            json=payload,
        )
    if response.status_code != 200 or not response.json().get("ok"):
        raise TelegramError(f"Telegram sendMessage failed ({response.status_code}): {response.text[:200]}")


async def edit_message_text(chat_id: str | int, message_id: int, text: str, reply_markup: dict | None = None) -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise TelegramError("Telegram is not configured (set TELEGRAM_BOT_TOKEN)")
    payload: dict = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/editMessageText",
            json=payload,
        )
    if response.status_code != 200 or not response.json().get("ok"):
        raise TelegramError(f"Telegram editMessageText failed ({response.status_code}): {response.text[:200]}")


async def delete_message(chat_id: str | int, message_id: int) -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        return
    async with httpx.AsyncClient(timeout=15) as client:
        await client.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/deleteMessage",
            json={"chat_id": chat_id, "message_id": message_id},
        )


async def answer_callback_query(callback_query_id: str) -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        return
    async with httpx.AsyncClient(timeout=15) as client:
        await client.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/answerCallbackQuery",
            json={"callback_query_id": callback_query_id},
        )
