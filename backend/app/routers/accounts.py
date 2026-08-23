from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import SecretBox, get_secret_box
from app.database import get_db
from app.dependencies import get_current_admin, get_sync_orchestrator
from app.repositories.account_repository import AccountRepository
from app.schemas.account import (
    AccountCreateRequest,
    AccountDiscoverDeploymentsRequest,
    AccountDiscoverRequest,
    AccountResourceDeploymentsRequest,
    AccountResponse,
    AccountUpdateRequest,
    DeploymentResponse,
    DiscoveredResourceResponse,
    SyncAllResponse,
)
from app.services.account_service import AccountNotFoundError, AccountService, DuplicateAccountError
from app.services.new_api_service import NewApiError, set_gateway_status
from app.services.sync_orchestrator import SyncOrchestrator

router = APIRouter(prefix="/api/accounts", tags=["accounts"], dependencies=[Depends(get_current_admin)])


def _service(db: AsyncSession = Depends(get_db), secret_box: SecretBox = Depends(get_secret_box)) -> AccountService:
    return AccountService(AccountRepository(db), secret_box)


@router.post("/discover", response_model=list[DiscoveredResourceResponse])
async def discover_resources(payload: AccountDiscoverRequest, service: AccountService = Depends(_service)):
    try:
        return await service.discover(payload)
    except Exception as exc:  # noqa: BLE001 - surface Azure auth/network errors to the admin UI
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/discover-deployments", response_model=list[DeploymentResponse])
async def discover_deployments(payload: AccountDiscoverDeploymentsRequest, service: AccountService = Depends(_service)):
    try:
        return await service.discover_deployments(payload)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("", response_model=list[AccountResponse])
async def list_accounts(service: AccountService = Depends(_service)):
    return await service.list_accounts()


@router.post("", response_model=AccountResponse)
async def create_account(payload: AccountCreateRequest, service: AccountService = Depends(_service)):
    try:
        return await service.create_account(payload)
    except DuplicateAccountError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{account_id}/deployments", response_model=list[DeploymentResponse])
async def list_deployments(account_id: int, service: AccountService = Depends(_service)):
    try:
        return await service.list_deployments(account_id)
    except AccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{account_id}/test")
async def test_account(account_id: int, service: AccountService = Depends(_service)):
    try:
        return await service.test_connection(account_id)
    except AccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{account_id}/sync")
async def sync_account(account_id: int, orchestrator: SyncOrchestrator = Depends(get_sync_orchestrator)):
    return await orchestrator.sync_one(account_id)


@router.post("/sync-all", response_model=SyncAllResponse)
async def sync_all_accounts(orchestrator: SyncOrchestrator = Depends(get_sync_orchestrator)):
    return await orchestrator.sync_all()


@router.post("/newapi-sync")
async def sync_new_api_channels(orchestrator: SyncOrchestrator = Depends(get_sync_orchestrator)):
    return await orchestrator.sync_new_api_safe()


def _validate_gateway(gateway: str | None) -> str | None:
    if gateway is not None and gateway not in ("O1", "O2"):
        raise HTTPException(status_code=422, detail="gateway must be O1 or O2")
    return gateway


@router.post("/{account_id}/gateway-enable")
async def enable_account_gateway(account_id: int, gateway: str | None = None, db: AsyncSession = Depends(get_db)):
    try:
        result = await set_gateway_status(db, account_id, 1, _validate_gateway(gateway))
    except NewApiError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result.get("status") != "ok":
        raise HTTPException(status_code=400, detail=_gateway_error_detail(result))
    return result


@router.post("/{account_id}/gateway-disable")
async def disable_account_gateway(account_id: int, gateway: str | None = None, db: AsyncSession = Depends(get_db)):
    try:
        result = await set_gateway_status(db, account_id, 2, _validate_gateway(gateway))
    except NewApiError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result.get("status") != "ok":
        raise HTTPException(status_code=400, detail=_gateway_error_detail(result))
    return result


def _gateway_error_detail(result: dict) -> str:
    errors = result.get("errors") or {}
    return "; ".join(f"{label}: {message}" for label, message in errors.items()) or "Partial gateway update"


@router.post("/{account_id}/discover", response_model=list[DiscoveredResourceResponse])
async def discover_account_resources(account_id: int, service: AccountService = Depends(_service)):
    try:
        return await service.discover_for_account(account_id)
    except AccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{account_id}/discover-deployments", response_model=list[DeploymentResponse])
async def discover_account_deployments(
    account_id: int,
    payload: AccountResourceDeploymentsRequest,
    service: AccountService = Depends(_service),
):
    try:
        return await service.discover_deployments_for_account(account_id, payload.resource_id)
    except AccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/{account_id}", response_model=AccountResponse)
async def update_account(account_id: int, payload: AccountUpdateRequest, service: AccountService = Depends(_service)):
    try:
        return await service.update_account(account_id, payload)
    except AccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DuplicateAccountError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/{account_id}")
async def delete_account(account_id: int, service: AccountService = Depends(_service)):
    try:
        await service.delete_account(account_id)
    except AccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "deleted"}
