import asyncio
import contextlib
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_admin
from app.schemas.kimi_deploy import (
    KimiBootstrapRequest,
    KimiCapacitySummary,
    KimiContentFilterResponse,
    KimiCreditsRequest,
    KimiCreditsResponse,
    KimiDeleteResponse,
    KimiDropStoredRequest,
    KimiDropStoredResponse,
    KimiDeployDefaults,
    KimiDeployJobSnapshot,
    KimiDeployRequest,
    KimiDeployResponse,
    KimiDeployResult,
    KimiDeployStatus,
    KimiNewApiAuth,
    KimiNewApiPool,
    KimiNewApiRenameRequest,
    KimiNewApiRequest,
    KimiRegenerateRequest,
    KimiRegenerateResponse,
    KimiSecretsRow,
    KimiSheetStatus,
    KimiSheetSyncRequest,
    KimiSheetSyncResponse,
    KimiStoredAccount,
    KimiStoredResponse,
    KimiTestResponse,
)
from app.services.deploy_defaults import get_deploy_defaults, resolve_routing, save_deploy_defaults
from app.services.deploy_job_runner import kimi_job_snapshot, start_kimi_deploy_job
from app.services.kimi_deploy_service import (
    KimiDeployError,
    add_kimi_newapi_channels,
    apply_content_filters,
    bootstrap_account,
    delete_accounts,
    deploy_accounts,
    deploy_status,
    lookup_accounts_credits,
    lookup_accounts_inventory,
    regenerate_accounts,
    scale_accounts,
    test_accounts,
)
from app.services.kimi_newapi import kimi_newapi_auth, kimi_newapi_pool, rename_kimi_newapi_channel
from app.services.service_principal_store import drop_stored_principal, list_service_principals

router = APIRouter(prefix="/api/kimi-deploy", tags=["kimi-deploy"], dependencies=[Depends(get_current_admin)])


@router.get("/status", response_model=KimiDeployStatus)
async def get_status() -> KimiDeployStatus:
    return deploy_status()


@router.get("/capacity", response_model=KimiCapacitySummary)
async def capacity() -> KimiCapacitySummary:
    from app.services.kimi_capacity import active_newapi_capacity

    return KimiCapacitySummary(**await active_newapi_capacity())


@router.get("/defaults", response_model=KimiDeployDefaults)
async def read_deploy_defaults(db: AsyncSession = Depends(get_db)) -> KimiDeployDefaults:
    return KimiDeployDefaults(**await get_deploy_defaults(db))


@router.put("/defaults", response_model=KimiDeployDefaults)
async def update_deploy_defaults(payload: KimiDeployDefaults, db: AsyncSession = Depends(get_db)) -> KimiDeployDefaults:
    try:
        return KimiDeployDefaults(**await save_deploy_defaults(db, payload.model_dump()))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/stored", response_model=KimiStoredResponse)
async def stored_accounts(db: AsyncSession = Depends(get_db)) -> KimiStoredResponse:
    rows = await list_service_principals(db)
    return KimiStoredResponse(
        accounts=[
            KimiStoredAccount(
                name=row.name,
                account_holder=row.account_holder,
                AZURE_TENANT_ID=row.tenant_id,
                AZURE_CLIENT_ID=row.client_id,
                AZURE_SUBSCRIPTION_ID=row.subscription_id,
                subscription_name=row.subscription_name,
                owner_tag=row.owner_tag,
                created_at=row.created_at,
            )
            for row in rows
        ]
    )


@router.post("/stored/drop", response_model=KimiDropStoredResponse)
async def drop_stored(
    payload: KimiDropStoredRequest, db: AsyncSession = Depends(get_db)
) -> KimiDropStoredResponse:
    from app.services.azure_inventory_cache import drop_azure_inventory

    dropped = 0
    for subscription_id in payload.subscription_ids:
        sub = (subscription_id or "").strip()
        if not sub:
            continue
        await drop_stored_principal(db, sub)
        await drop_azure_inventory(sub)
        dropped += 1
    return KimiDropStoredResponse(ok=True, dropped=dropped)


@router.post("/bootstrap", response_model=KimiSecretsRow)
async def bootstrap(payload: KimiBootstrapRequest, db: AsyncSession = Depends(get_db)) -> KimiSecretsRow:
    try:
        return await bootstrap_account(payload.name, payload.email, session=db)
    except KimiDeployError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/credits", response_model=KimiCreditsResponse)
async def credits(payload: KimiCreditsRequest, db: AsyncSession = Depends(get_db)) -> KimiCreditsResponse:
    try:
        results = await lookup_accounts_credits(payload.accounts, session=db)
    except KimiDeployError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return KimiCreditsResponse(results=results)


@router.post("/inventory", response_model=KimiDeployResponse)
async def inventory(payload: KimiCreditsRequest, db: AsyncSession = Depends(get_db)) -> KimiDeployResponse:
    try:
        results = await lookup_accounts_inventory(payload.accounts, session=db, refresh=payload.refresh)
    except KimiDeployError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ok_count = sum(1 for item in results if item.ok)
    return KimiDeployResponse(ok_count=ok_count, fail_count=len(results) - ok_count, results=results)


@router.post("/test", response_model=KimiTestResponse)
async def test_model(payload: KimiCreditsRequest, db: AsyncSession = Depends(get_db)) -> KimiTestResponse:
    try:
        results = await test_accounts(payload.accounts, session=db)
    except KimiDeployError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ok_count = sum(1 for item in results if item.ok)
    return KimiTestResponse(ok_count=ok_count, fail_count=len(results) - ok_count, results=results)


@router.get("/newapi", response_model=KimiNewApiPool)
async def newapi_pool() -> KimiNewApiPool:
    return await kimi_newapi_pool()


@router.get("/newapi/auth", response_model=KimiNewApiAuth)
async def newapi_auth() -> KimiNewApiAuth:
    return await kimi_newapi_auth()


@router.post("/newapi", response_model=KimiDeployResponse)
async def add_newapi(payload: KimiNewApiRequest, db: AsyncSession = Depends(get_db)) -> KimiDeployResponse:
    try:
        priority, weight = await resolve_routing(db, payload.priority, payload.weight)
        results = await add_kimi_newapi_channels(
            payload.accounts,
            db,
            priority=priority,
            weight=weight,
        )
    except KimiDeployError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ok_count = sum(1 for item in results if item.new_api_present)
    return KimiDeployResponse(ok_count=ok_count, fail_count=len(results) - ok_count, results=results)


@router.get("/sheet", response_model=KimiSheetStatus)
async def sheet_status() -> KimiSheetStatus:
    from app.services.google_sheet_inventory import configured

    return KimiSheetStatus(configured=configured())


@router.post("/sheet", response_model=KimiSheetSyncResponse)
async def sync_sheet(payload: KimiSheetSyncRequest) -> KimiSheetSyncResponse:
    from app.services.google_sheet_inventory import SheetSyncError, configured, push_inventory

    if not configured():
        raise HTTPException(
            status_code=400,
            detail="Google Sheet is not configured. Set GOOGLE_SHEETS_SPREADSHEET_ID and a service-account key.",
        )
    try:
        synced = await push_inventory(payload.results)
    except SheetSyncError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return KimiSheetSyncResponse(ok=True, configured=True, synced=synced)


@router.post("/newapi/rename", response_model=KimiDeployResult)
async def rename_newapi(payload: KimiNewApiRenameRequest, db: AsyncSession = Depends(get_db)) -> KimiDeployResult:
    try:
        result = await rename_kimi_newapi_channel(
            db,
            name=payload.name,
            priority=payload.priority,
            weight=payload.weight,
            channel_id=payload.channel_id,
            subscription_id=payload.subscription_id,
            resource_name=payload.account_name,
            endpoint=payload.azure_openai_endpoint or None,
        )
    except KimiDeployError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.new_api_error or "Could not update the NewAPI channel.")
    return result


@router.post("/regenerate-keys", response_model=KimiRegenerateResponse)
async def regenerate_keys(payload: KimiRegenerateRequest, db: AsyncSession = Depends(get_db)) -> KimiRegenerateResponse:
    try:
        results = await regenerate_accounts(payload.accounts, payload.jobs, session=db)
    except KimiDeployError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ok_count = sum(1 for item in results if item.ok)
    return KimiRegenerateResponse(ok_count=ok_count, fail_count=len(results) - ok_count, results=results)


@router.post("/undeploy", response_model=KimiDeleteResponse)
async def undeploy(payload: KimiRegenerateRequest, db: AsyncSession = Depends(get_db)) -> KimiDeleteResponse:
    try:
        results = await delete_accounts(payload.accounts, payload.jobs, session=db)
    except KimiDeployError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ok_count = sum(1 for item in results if item.ok)
    return KimiDeleteResponse(ok_count=ok_count, fail_count=len(results) - ok_count, results=results)


@router.post("/content-filter", response_model=KimiContentFilterResponse)
async def content_filter(payload: KimiRegenerateRequest, db: AsyncSession = Depends(get_db)) -> KimiContentFilterResponse:
    try:
        results = await apply_content_filters(payload.accounts, payload.jobs, session=db)
    except KimiDeployError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ok_count = sum(1 for item in results if item.ok)
    return KimiContentFilterResponse(ok_count=ok_count, fail_count=len(results) - ok_count, results=results)


@router.post("/scale-quota", response_model=KimiDeployResponse)
async def scale_quota(payload: KimiRegenerateRequest, db: AsyncSession = Depends(get_db)) -> KimiDeployResponse:
    try:
        results = await scale_accounts(payload.accounts, payload.jobs, session=db)
    except KimiDeployError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ok_count = sum(1 for item in results if item.ok)
    return KimiDeployResponse(ok_count=ok_count, fail_count=len(results) - ok_count, results=results)


@router.get("/job", response_model=KimiDeployJobSnapshot)
async def deploy_job_status() -> KimiDeployJobSnapshot:
    return kimi_job_snapshot()


@router.post("/job", response_model=KimiDeployJobSnapshot)
async def start_deploy_job(payload: KimiDeployRequest, db: AsyncSession = Depends(get_db)) -> KimiDeployJobSnapshot:
    try:
        priority, weight = await resolve_routing(db, payload.new_api_priority, payload.new_api_weight)
        return await start_kimi_deploy_job(
            payload.accounts,
            payload.jobs,
            priority,
            weight,
        )
    except KimiDeployError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/stream")
async def deploy_stream(payload: KimiDeployRequest, db: AsyncSession = Depends(get_db)) -> StreamingResponse:
    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def on_progress(event: dict) -> None:
        await queue.put(event)

    async def run() -> None:
        try:
            priority, weight = await resolve_routing(db, payload.new_api_priority, payload.new_api_weight)
            results = await deploy_accounts(
                payload.accounts,
                payload.jobs,
                session=db,
                new_api_priority=priority,
                new_api_weight=weight,
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
        except KimiDeployError as exc:
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


@router.post("", response_model=KimiDeployResponse)
async def deploy(payload: KimiDeployRequest, db: AsyncSession = Depends(get_db)) -> KimiDeployResponse:
    try:
        priority, weight = await resolve_routing(db, payload.new_api_priority, payload.new_api_weight)
        results = await deploy_accounts(
            payload.accounts,
            payload.jobs,
            session=db,
            new_api_priority=priority,
            new_api_weight=weight,
        )
    except KimiDeployError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ok_count = sum(1 for item in results if item.ok)
    return KimiDeployResponse(ok_count=ok_count, fail_count=len(results) - ok_count, results=results)
