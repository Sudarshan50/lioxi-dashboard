from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_admin
from app.repositories.account_group_repository import AccountGroupRepository
from app.repositories.account_repository import AccountRepository
from app.schemas.account_group import AccountGroupCreateRequest, AccountGroupResponse, AccountGroupUpdateRequest
from app.services.account_group_service import (
    AccountGroupNotFoundError,
    AccountGroupService,
    DuplicateAccountGroupError,
)

router = APIRouter(prefix="/api/account-groups", tags=["account-groups"], dependencies=[Depends(get_current_admin)])


def _service(db: AsyncSession = Depends(get_db)) -> AccountGroupService:
    return AccountGroupService(AccountGroupRepository(db), AccountRepository(db))


@router.get("", response_model=list[AccountGroupResponse])
async def list_groups(service: AccountGroupService = Depends(_service)):
    return await service.list_groups()


@router.post("", response_model=AccountGroupResponse)
async def create_group(payload: AccountGroupCreateRequest, service: AccountGroupService = Depends(_service)):
    try:
        return await service.create_group(payload)
    except DuplicateAccountGroupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.patch("/{group_id}", response_model=AccountGroupResponse)
async def update_group(
    group_id: int, payload: AccountGroupUpdateRequest, service: AccountGroupService = Depends(_service)
):
    try:
        return await service.update_group(group_id, payload)
    except AccountGroupNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/{group_id}")
async def delete_group(group_id: int, service: AccountGroupService = Depends(_service)):
    try:
        await service.delete_group(group_id)
    except AccountGroupNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "deleted"}
