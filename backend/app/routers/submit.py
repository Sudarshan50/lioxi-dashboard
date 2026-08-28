import asyncio
import contextlib
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.submit import SubmitCommitRequest, SubmitNamesResponse, SubmitSessionCreated, SubmitSessionSnapshot
from app.services.submit_service import (
    SubmitError,
    commit_session,
    create_session,
    expire_stale,
    get_request,
    list_owner_names,
    public_snapshot,
    subscribe,
    unsubscribe,
)

router = APIRouter(prefix="/api/submit", tags=["submit"])


def _sse(events):
    return StreamingResponse(
        events,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _event_stream(session_id: str, db: AsyncSession):
    queue = await subscribe(session_id)
    try:
        row = await get_request(db, session_id)
        if row is not None:
            snap = public_snapshot(row)
            yield f"data: {json.dumps({'type': 'snapshot', **snap.model_dump(mode='json')}, default=str)}\n\n"
            if row.status in {"logged_in", "pending_approval", "approved"}:
                return
            if row.status in {"failed", "expired", "rejected"}:
                yield f"data: {json.dumps({'type': 'error', 'detail': row.error_message or 'Session ended.'})}\n\n"
                return
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=15)
            except TimeoutError:
                yield ": ping\n\n"
                continue
            if item is None:
                break
            yield f"data: {json.dumps(item, default=str)}\n\n"
            if item.get("type") in {"done", "error", "logged_in"}:
                break
    finally:
        await unsubscribe(session_id, queue)


@router.post("/sessions", response_model=SubmitSessionCreated)
async def start_session(
    db: AsyncSession = Depends(get_db),
    tenant_id: str | None = Query(default=None, max_length=64),
) -> SubmitSessionCreated:
    try:
        row = await create_session(db, tenant_id=tenant_id)
    except SubmitError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)[:400]) from exc
    return SubmitSessionCreated(session_id=row.session_id, status=row.status)


@router.get("/sessions/{session_id}", response_model=SubmitSessionSnapshot)
async def session_status(session_id: str, db: AsyncSession = Depends(get_db)) -> SubmitSessionSnapshot:
    await expire_stale(db)
    row = await get_request(db, session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown submit session.")
    return public_snapshot(row)


@router.get("/sessions/{session_id}/events")
async def session_events(session_id: str, db: AsyncSession = Depends(get_db)) -> StreamingResponse:
    row = await get_request(db, session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown submit session.")
    return _sse(_event_stream(session_id, db))


@router.post("/sessions/{session_id}/commit")
async def commit(session_id: str, payload: SubmitCommitRequest, db: AsyncSession = Depends(get_db)) -> StreamingResponse:
    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def on_progress(event: dict) -> None:
        await queue.put(event)

    async def run() -> None:
        try:
            await commit_session(
                db,
                session_id,
                payload.subscription_id,
                payload.person_associated,
                on_progress=on_progress,
            )
        except SubmitError as exc:
            await queue.put({"type": "error", "detail": str(exc)})
        except Exception as exc:  # noqa: BLE001
            await queue.put({"type": "error", "detail": str(exc)[:400]})
        finally:
            await queue.put(None)

    async def events():
        task = asyncio.create_task(run())
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield ": ping\n\n"
                    continue
                if item is None:
                    break
                yield f"data: {json.dumps(item, default=str)}\n\n"
        finally:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    return _sse(events())


@router.get("/names", response_model=SubmitNamesResponse)
async def names(db: AsyncSession = Depends(get_db)) -> SubmitNamesResponse:
    return SubmitNamesResponse(names=await list_owner_names(db))
