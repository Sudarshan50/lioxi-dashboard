from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_admin
from app.schemas.gpt_deploy import (
    GptAccountRow,
    GptAvailability,
    GptDeployRequest,
    GptJob,
    GptLogList,
    GptLogRow,
)
from app.services.gpt_deploy_service import (
    GptDeployError,
    account_availability,
    job_snapshot,
    list_gpt_accounts,
    list_logs,
    start_gpt_deploy,
)

router = APIRouter(prefix="/api/gpt-deploy", tags=["gpt-deploy"], dependencies=[Depends(get_current_admin)])


def _error(exc: GptDeployError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/accounts", response_model=list[GptAccountRow])
async def accounts(db: AsyncSession = Depends(get_db)) -> list[GptAccountRow]:
    return [GptAccountRow(**row) for row in await list_gpt_accounts(db)]


@router.get("/accounts/{account_id}/availability", response_model=GptAvailability)
async def availability(account_id: int, db: AsyncSession = Depends(get_db)) -> GptAvailability:
    try:
        return GptAvailability(**await account_availability(db, account_id))
    except GptDeployError as exc:
        raise _error(exc) from exc


@router.post("/jobs", response_model=GptJob)
async def start_job(payload: GptDeployRequest) -> GptJob:
    ids = list(payload.account_ids)
    if payload.account_id is not None:
        ids.append(payload.account_id)
    try:
        return GptJob(**await start_gpt_deploy(ids, payload.stack, payload.models))
    except GptDeployError as exc:
        raise _error(exc) from exc


@router.get("/jobs/current", response_model=GptJob)
async def current_job() -> GptJob:
    return GptJob(**job_snapshot())


@router.get("/logs", response_model=GptLogList)
async def logs(
    account_id: int | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> GptLogList:
    rows = await list_logs(db, account_id)
    return GptLogList(
        items=[
            GptLogRow(
                id=row.id,
                account_id=row.account_id,
                account_name=row.account_name,
                action=row.action,
                level=row.level,
                message=row.message,
                created_at=row.created_at,
            )
            for row in rows
        ]
    )
