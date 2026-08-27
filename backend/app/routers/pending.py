import asyncio
import contextlib
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_admin
from app.schemas.submit import PendingApproveRequest, PendingDeclineResponse, PendingListResponse, PendingRequestPublic
from app.services.submit_service import (
    SubmitError,
    approve_request,
    list_pending,
    pending_public,
    reject_request,
)

router = APIRouter(prefix="/api/pending", tags=["pending"], dependencies=[Depends(get_current_admin)])


@router.get("", response_model=PendingListResponse)
async def pending_list(db: AsyncSession = Depends(get_db)) -> PendingListResponse:
    rows = await list_pending(db)
    public = [pending_public(row) for row in rows]
    return PendingListResponse(
        requests=public,
        pending_count=sum(1 for row in public if row.status == "pending_approval"),
        failed_count=sum(1 for row in public if row.status == "failed"),
    )


@router.post("/{request_id}/reject", response_model=PendingDeclineResponse)
@router.post("/{request_id}/decline", response_model=PendingDeclineResponse)
async def pending_reject(request_id: int, db: AsyncSession = Depends(get_db)) -> PendingDeclineResponse:
    try:
        deleted_id, subscription_id = await reject_request(db, request_id)
    except SubmitError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PendingDeclineResponse(ok=True, deleted_id=deleted_id, subscription_id=subscription_id)


@router.post("/{request_id}/approve")
async def pending_approve(
    request_id: int,
    payload: PendingApproveRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    body = payload or PendingApproveRequest()
    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def on_progress(event: dict) -> None:
        await queue.put(event)

    async def run() -> None:
        try:
            results = await approve_request(
                db,
                request_id,
                jobs=body.jobs,
                new_api_priority=body.new_api_priority,
                new_api_weight=body.new_api_weight,
                on_progress=on_progress,
            )
            await queue.put(
                {
                    "type": "done",
                    "total": len(results),
                    "phase": "done",
                    "results": [item.model_dump(mode="json") for item in results],
                }
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

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
