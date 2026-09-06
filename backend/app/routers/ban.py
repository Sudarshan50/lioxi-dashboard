from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_admin
from app.schemas.join_enrollee import (
    BanSettingsResponse,
    JoinAutoApproveRequest,
    JoinAutoApproveResponse,
    JoinEnrolleeCreateRequest,
    JoinEnrolleePublic,
    JoinEnrolleeUpdateRequest,
)
from app.services.join_enrollee_service import (
    EnrolleeError,
    create_enrollee,
    is_auto_approve_enabled,
    list_enrollees,
    save_auto_approve,
    set_enrollee_banned,
)
from app.services.submit_service import SubmitError, kick_auto_approve_queue

router = APIRouter(prefix="/api/ban", tags=["ban"], dependencies=[Depends(get_current_admin)])


def _public(row) -> JoinEnrolleePublic:
    return JoinEnrolleePublic(
        id=row.id,
        name=row.name,
        banned=row.banned,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("", response_model=BanSettingsResponse)
async def ban_settings(db: AsyncSession = Depends(get_db)) -> BanSettingsResponse:
    names = await list_enrollees(db)
    return BanSettingsResponse(
        auto_approve=await is_auto_approve_enabled(db),
        names=[_public(row) for row in names],
    )


@router.put("/auto-approve", response_model=JoinAutoApproveResponse)
async def update_auto_approve(
    payload: JoinAutoApproveRequest,
    db: AsyncSession = Depends(get_db),
) -> JoinAutoApproveResponse:
    enabled = await save_auto_approve(db, payload.enabled)
    started: list[int] = []
    skipped: list[int] = []
    if enabled:
        try:
            started, skipped_pairs = await kick_auto_approve_queue(db)
            skipped = [item_id for item_id, _error in skipped_pairs]
        except SubmitError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JoinAutoApproveResponse(auto_approve=enabled, started=started, skipped=skipped)


@router.post("/names", response_model=JoinEnrolleePublic)
async def add_name(payload: JoinEnrolleeCreateRequest, db: AsyncSession = Depends(get_db)) -> JoinEnrolleePublic:
    try:
        row = await create_enrollee(db, payload.name)
    except EnrolleeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _public(row)


@router.patch("/names/{enrollee_id}", response_model=JoinEnrolleePublic)
async def update_name(
    enrollee_id: int,
    payload: JoinEnrolleeUpdateRequest,
    db: AsyncSession = Depends(get_db),
) -> JoinEnrolleePublic:
    try:
        row = await set_enrollee_banned(db, enrollee_id, payload.banned)
    except EnrolleeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _public(row)
