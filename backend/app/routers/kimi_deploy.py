from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_current_admin
from app.schemas.kimi_deploy import (
    KimiBootstrapRequest,
    KimiCreditsRequest,
    KimiCreditsResponse,
    KimiDeleteResponse,
    KimiDeployRequest,
    KimiDeployResponse,
    KimiDeployStatus,
    KimiRegenerateRequest,
    KimiRegenerateResponse,
    KimiSecretsRow,
    KimiTestResponse,
)
from app.services.kimi_deploy_service import (
    KimiDeployError,
    bootstrap_account,
    delete_accounts,
    deploy_accounts,
    deploy_status,
    lookup_accounts_credits,
    lookup_accounts_inventory,
    regenerate_accounts,
    test_accounts,
)

router = APIRouter(prefix="/api/kimi-deploy", tags=["kimi-deploy"], dependencies=[Depends(get_current_admin)])


@router.get("/status", response_model=KimiDeployStatus)
async def get_status() -> KimiDeployStatus:
    return deploy_status()


@router.post("/bootstrap", response_model=KimiSecretsRow)
async def bootstrap(payload: KimiBootstrapRequest) -> KimiSecretsRow:
    try:
        return await bootstrap_account(payload.name, payload.email)
    except KimiDeployError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/credits", response_model=KimiCreditsResponse)
async def credits(payload: KimiCreditsRequest) -> KimiCreditsResponse:
    try:
        results = await lookup_accounts_credits(payload.accounts)
    except KimiDeployError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return KimiCreditsResponse(results=results)


@router.post("/inventory", response_model=KimiDeployResponse)
async def inventory(payload: KimiCreditsRequest) -> KimiDeployResponse:
    try:
        results = await lookup_accounts_inventory(payload.accounts)
    except KimiDeployError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ok_count = sum(1 for item in results if item.ok)
    return KimiDeployResponse(ok_count=ok_count, fail_count=len(results) - ok_count, results=results)


@router.post("/test", response_model=KimiTestResponse)
async def test_model(payload: KimiCreditsRequest) -> KimiTestResponse:
    try:
        results = await test_accounts(payload.accounts)
    except KimiDeployError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ok_count = sum(1 for item in results if item.ok)
    return KimiTestResponse(ok_count=ok_count, fail_count=len(results) - ok_count, results=results)


@router.post("/regenerate-keys", response_model=KimiRegenerateResponse)
async def regenerate_keys(payload: KimiRegenerateRequest) -> KimiRegenerateResponse:
    try:
        results = await regenerate_accounts(payload.accounts, payload.jobs)
    except KimiDeployError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ok_count = sum(1 for item in results if item.ok)
    return KimiRegenerateResponse(ok_count=ok_count, fail_count=len(results) - ok_count, results=results)


@router.post("/undeploy", response_model=KimiDeleteResponse)
async def undeploy(payload: KimiRegenerateRequest) -> KimiDeleteResponse:
    try:
        results = await delete_accounts(payload.accounts, payload.jobs)
    except KimiDeployError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ok_count = sum(1 for item in results if item.ok)
    return KimiDeleteResponse(ok_count=ok_count, fail_count=len(results) - ok_count, results=results)


@router.post("", response_model=KimiDeployResponse)
async def deploy(payload: KimiDeployRequest) -> KimiDeployResponse:
    try:
        results = await deploy_accounts(payload.accounts, payload.jobs)
    except KimiDeployError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ok_count = sum(1 for item in results if item.ok)
    return KimiDeployResponse(ok_count=ok_count, fail_count=len(results) - ok_count, results=results)
