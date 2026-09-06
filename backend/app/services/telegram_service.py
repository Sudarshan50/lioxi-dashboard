"""Thin client for Telegram bot notifications (group alerts)."""

import asyncio
import html
import logging
from datetime import datetime, timedelta, timezone

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
DELETE_BATCH_SIZE = 100
HIGH_WATER_KEY = "telegram_group_last_message_id"
TIP_SEARCH_CEILING = 2_000_000
_clear_guard = asyncio.Lock()
_clear_job: dict = {}
_clear_task: asyncio.Task | None = None
_group_high_water = 0
_notice_tasks: set[asyncio.Task] = set()


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


def format_k3_deployed_notice(person: str, account: str, authorized_by: str) -> str:
    who = html.escape((person or "").strip() or "Unknown")
    name = html.escape((account or "").strip() or "account")
    auth = html.escape((authorized_by or "admin").strip() or "admin")
    return f"<b>K3 deployed</b>\n{who} · <code>{name}</code>\nAuthorized: {auth}"


def schedule_k3_deployed_notice(person: str, account: str, authorized_by: str) -> None:
    task = asyncio.create_task(_notify_k3_deployed(person, account, authorized_by))
    _notice_tasks.add(task)
    task.add_done_callback(_notice_tasks.discard)


async def _notify_k3_deployed(person: str, account: str, authorized_by: str) -> None:
    if not is_configured():
        return
    try:
        await send_message(format_k3_deployed_notice(person, account, authorized_by))
    except Exception:
        logger.exception("Telegram K3 deploy notice failed person=%s account=%s", person, account)


def _is_group_chat(chat_id: str | int | None) -> bool:
    settings = get_settings()
    group_id = str(settings.telegram_chat_id or "").strip()
    return bool(group_id and chat_id is not None and str(chat_id) == group_id)


def note_group_message_id(chat_id: str | int | None, *message_ids: int | None) -> None:
    global _group_high_water
    if not _is_group_chat(chat_id):
        return
    for raw in message_ids:
        if raw is None:
            continue
        _group_high_water = max(_group_high_water, int(raw))


def group_clear_running() -> bool:
    return bool(_clear_job.get("running"))


async def send_message(
    text: str,
    chat_id: str | int | None = None,
    reply_markup: dict | None = None,
    message_thread_id: int | None = None,
) -> int | None:
    settings = get_settings()
    target = chat_id or settings.telegram_chat_id
    if not settings.telegram_bot_token or not target:
        raise TelegramError("Telegram is not configured (set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)")
    payload: dict = {
        "chat_id": target,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if message_thread_id is not None:
        payload["message_thread_id"] = int(message_thread_id)
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
            json=payload,
        )
    data = response.json()
    if response.status_code != 200 or not data.get("ok"):
        raise TelegramError(f"Telegram sendMessage failed ({response.status_code}): {response.text[:200]}")
    message_id = (data.get("result") or {}).get("message_id")
    if message_id is not None:
        note_group_message_id(target, int(message_id))
        return int(message_id)
    return None


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
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/editMessageText",
            json=payload,
        )
    data = response.json() if response.content else {}
    if response.status_code == 200 and data.get("ok"):
        return
    description = str(data.get("description") or response.text)
    if "message is not modified" in description.lower():
        return
    raise TelegramError(f"Telegram editMessageText failed ({response.status_code}): {description[:200]}")


def _retry_after(data: dict) -> float:
    wait = (data.get("parameters") or {}).get("retry_after")
    try:
        return min(max(float(wait), 0.2), 3.0)
    except (TypeError, ValueError):
        return 1.0


def _delete_status(response: httpx.Response) -> str:
    data = response.json() if response.content else {}
    description = str(data.get("description") or "").lower()
    if response.status_code == 429 or data.get("error_code") == 429:
        return "retry"
    if response.status_code == 200 and data.get("ok"):
        return "deleted"
    if "message to delete not found" in description or "message identifier is invalid" in description:
        return "missing"
    if "can't be deleted" in description or "message can't be deleted" in description:
        return "exists"
    return "error"


async def _post_telegram(
    session: httpx.AsyncClient, method: str, payload: dict, *, attempts: int = 5
) -> httpx.Response:
    settings = get_settings()
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/{method}"
    last = None
    for _attempt in range(attempts):
        last = await session.post(url, json=payload)
        data = last.json() if last.content else {}
        if last.status_code == 429 or data.get("error_code") == 429:
            await asyncio.sleep(_retry_after(data))
            continue
        return last
    return last or httpx.Response(429, json={"ok": False})


async def delete_message(chat_id: str | int, message_id: int, *, client: httpx.AsyncClient | None = None) -> bool:
    settings = get_settings()
    if not settings.telegram_bot_token:
        return False
    own_client = client is None
    session = client or httpx.AsyncClient(timeout=15)
    try:
        response = await _post_telegram(
            session, "deleteMessage", {"chat_id": chat_id, "message_id": message_id}
        )
        status = _delete_status(response)
        if status == "deleted":
            return True
        if status not in ("missing", "exists"):
            logger.warning(
                "Telegram deleteMessage failed chat=%s message=%s: %s",
                chat_id,
                message_id,
                (response.json() if response.content else {}).get("description") or response.text[:200],
            )
        return False
    finally:
        if own_client:
            await session.aclose()


async def _probe_one(session: httpx.AsyncClient, chat_id: str | int, message_id: int) -> str:
    response = await _post_telegram(
        session, "deleteMessage", {"chat_id": chat_id, "message_id": int(message_id)}
    )
    return _delete_status(response)


async def delete_messages(
    chat_id: str | int,
    message_ids: list[int],
    *,
    client: httpx.AsyncClient | None = None,
) -> bool:
    """Delete up to 100 messages. Falls back to one-by-one so older posts are not skipped."""
    settings = get_settings()
    ids = [int(message_id) for message_id in message_ids if message_id]
    if not settings.telegram_bot_token or not ids:
        return False
    own_client = client is None
    session = client or httpx.AsyncClient(timeout=20)
    try:
        response = await _post_telegram(
            session, "deleteMessages", {"chat_id": chat_id, "message_ids": ids[:DELETE_BATCH_SIZE]}
        )
        status = _delete_status(response)
        if status == "deleted":
            return True
        ok = True
        for message_id in ids:
            one = await _probe_one(session, chat_id, message_id)
            if one == "error":
                ok = False
        return ok
    finally:
        if own_client:
            await session.aclose()


def _id_batches_newest_first(newest_id: int, batch_size: int = DELETE_BATCH_SIZE) -> list[list[int]]:
    batches: list[list[int]] = []
    current = int(newest_id)
    while current >= 1:
        start = max(1, current - batch_size + 1)
        batches.append(list(range(start, current + 1)))
        current = start - 1
    return batches


async def _load_high_water() -> int:
    global _group_high_water
    from app.database import SessionLocal
    from app.models.app_setting import AppSetting

    async with SessionLocal() as session:
        row = await session.get(AppSetting, HIGH_WATER_KEY)
    if row is not None:
        try:
            _group_high_water = max(_group_high_water, int(row.value))
        except (TypeError, ValueError):
            pass
    return _group_high_water


async def _save_high_water() -> None:
    if _group_high_water <= 0:
        return
    from app.database import SessionLocal
    from app.models.app_setting import AppSetting

    async with SessionLocal() as session:
        row = await session.get(AppSetting, HIGH_WATER_KEY)
        if row is None:
            session.add(AppSetting(key=HIGH_WATER_KEY, value=str(_group_high_water)))
        elif int(row.value or 0) < _group_high_water:
            row.value = str(_group_high_water)
        await session.commit()


async def _id_exists(session: httpx.AsyncClient, chat_id: str | int, message_id: int) -> bool:
    status = await _probe_one(session, chat_id, message_id)
    return status in ("deleted", "exists")


async def _window_exists(session: httpx.AsyncClient, chat_id: str | int, mid: int) -> int:
    """Return the highest existing id in a small window around `mid`, or 0."""
    best = 0
    for delta in (0, -1, 1, -3, 3, -8, 8):
        probe = mid + delta
        if probe < 1:
            continue
        if await _id_exists(session, chat_id, probe):
            best = max(best, probe)
    return best


async def _discover_tip(session: httpx.AsyncClient, chat_id: str | int) -> int:
    """Use the last known group message id. Binary-search only when none is stored."""
    await _load_high_water()
    tip = _group_high_water
    _touch_clear_job(phase="finding", attempted=0, total=0)
    probes = 0
    if tip < 1:
        low, high = 1, TIP_SEARCH_CEILING
        while low <= high:
            mid = (low + high) // 2
            probes += 1
            _touch_clear_job(attempted=probes, phase="finding")
            found = await _window_exists(session, chat_id, mid)
            if found:
                tip = max(tip, found)
                low = mid + 1
            else:
                high = mid - 1
    for extra in range(1, 81):
        probes += 1
        if await _id_exists(session, chat_id, tip + extra):
            tip = tip + extra
        elif extra >= 15:
            break
    _touch_clear_job(attempted=probes, phase="finding")
    note_group_message_id(chat_id, tip)
    return tip


async def clear_chat(chat_id: str | int, *, kind: str = "group", newest_id: int | None = None) -> dict:
    """Delete every message ID from 1 through the latest. Does not post anything."""
    async with httpx.AsyncClient(timeout=20) as client:
        tip = int(newest_id) if newest_id else await _discover_tip(client, chat_id)
        if tip < 1:
            raise TelegramError("No group messages are known yet, so a silent full clear cannot start.")
        note_group_message_id(chat_id, tip)
        attempted = 0
        failed_batches = 0
        _touch_clear_job(newest_id=tip, total=tip, attempted=0, failed_batches=0, phase="deleting")
        for ids in _id_batches_newest_first(tip):
            attempted += len(ids)
            if not await delete_messages(chat_id, ids, client=client):
                failed_batches += 1
            _touch_clear_job(attempted=attempted, failed_batches=failed_batches)
    await _save_high_water()
    return {
        "chat_id": str(chat_id),
        "kind": kind,
        "newest_id": tip,
        "attempted": attempted,
        "failed_batches": failed_batches,
        "ok": failed_batches == 0,
    }


def clear_group_snapshot() -> dict:
    job = _clear_job or {}
    return {
        "running": bool(job.get("running")),
        "phase": job.get("phase") or ("deleting" if job.get("total") else "finding"),
        "newest_id": job.get("newest_id"),
        "attempted": int(job.get("attempted") or 0),
        "total": int(job.get("total") or 0),
        "failed_batches": int(job.get("failed_batches") or 0),
        "ok": job.get("ok"),
        "error": job.get("error"),
    }


def _touch_clear_job(**fields) -> None:
    if _clear_job:
        _clear_job.update(fields)


async def start_clear_group_chat() -> dict:
    """Start a full wipe of the linked group. Returns immediately; poll the snapshot."""
    global _clear_job, _clear_task
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise TelegramError("Telegram is not configured (set TELEGRAM_BOT_TOKEN)")
    if not settings.telegram_chat_id:
        raise TelegramError("No Telegram group is linked (set TELEGRAM_CHAT_ID)")
    async with _clear_guard:
        if _clear_job.get("running"):
            raise TelegramError("A group clear is already running.")
        _clear_job = {
            "running": True,
            "phase": "finding",
            "newest_id": None,
            "attempted": 0,
            "total": 0,
            "failed_batches": 0,
            "ok": None,
            "error": None,
        }
        _clear_task = asyncio.create_task(_run_clear_group_chat(str(settings.telegram_chat_id)))
    return clear_group_snapshot()


async def cancel_clear_group_chat() -> dict:
    task = _clear_task
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    _touch_clear_job(running=False, ok=False, error="Cancelled", phase="cancelled")
    return clear_group_snapshot()


async def _run_clear_group_chat(chat_id: str) -> None:
    try:
        from app.services.telegram_bot import cancel_all_self_destructs

        cancel_all_self_destructs()
        result = await clear_chat(chat_id, kind="group")
        _touch_clear_job(ok=result["ok"], newest_id=result["newest_id"], attempted=result["attempted"], failed_batches=result["failed_batches"], error=None, phase="done")
    except asyncio.CancelledError:
        _touch_clear_job(ok=False, error="Cancelled", phase="cancelled")
        raise
    except Exception as exc:
        logger.exception("Full group chat clear failed")
        _touch_clear_job(ok=False, error=str(exc)[:400], phase="error")
    finally:
        _touch_clear_job(running=False)


async def answer_callback_query(callback_query_id: str) -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        return
    async with httpx.AsyncClient(timeout=15) as client:
        await client.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/answerCallbackQuery",
            json={"callback_query_id": callback_query_id},
        )
