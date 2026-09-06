from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_admin
from app.schemas.submit import (
    PendingApproveAccepted,
    PendingApproveRequest,
    PendingBatchApproveRequest,
    PendingBatchApproveResponse,
    PendingBatchSkipped,
    PendingDeclineResponse,
    PendingListResponse,
)
from app.services.submit_service import (
    SubmitError,
    enqueue_approve,
    enqueue_approve_many,
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


@router.post("/approve-batch", response_model=PendingBatchApproveResponse)
async def pending_approve_batch(
    payload: PendingBatchApproveRequest,
    db: AsyncSession = Depends(get_db),
) -> PendingBatchApproveResponse:
    try:
        started, skipped = await enqueue_approve_many(
            db,
            payload.ids,
            retry=payload.retry,
            new_api_priority=payload.new_api_priority,
            new_api_weight=payload.new_api_weight,
        )
    except SubmitError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PendingBatchApproveResponse(
        ok=True,
        started=started,
        skipped=[PendingBatchSkipped(id=item_id, error=error) for item_id, error in skipped],
    )


@router.post("/{request_id}/approve", response_model=PendingApproveAccepted)
async def pending_approve(
    request_id: int,
    payload: PendingApproveRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> PendingApproveAccepted:
    body = payload or PendingApproveRequest()
    try:
        row = await enqueue_approve(
            db,
            request_id,
            new_api_priority=body.new_api_priority,
            new_api_weight=body.new_api_weight,
        )
    except SubmitError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PendingApproveAccepted(ok=True, request_id=row.id, status=row.status)
