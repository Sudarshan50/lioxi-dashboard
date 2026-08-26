from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_admin
from app.repositories.account_group_repository import AccountGroupRepository
from app.repositories.account_repository import AccountRepository
from app.repositories.model_repository import ModelRepository
from app.repositories.usage_repository import UsageRepository
from app.schemas.usage import AccountTpmPoint, BreakdownItem, DashboardOverview, FxRate, TimeseriesPoint
from app.services.dashboard_service import DashboardService
from app.services.fx_service import usd_to_inr_quote

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"], dependencies=[Depends(get_current_admin)])


def _service(db: AsyncSession = Depends(get_db)) -> DashboardService:
    return DashboardService(UsageRepository(db), AccountRepository(db), ModelRepository(db), AccountGroupRepository(db))


@router.get("/overview", response_model=DashboardOverview)
async def overview(
    range: str = "7d",
    account_id: int | None = None,
    model_id: int | None = None,
    group_id: int | None = None,
    gateway: str | None = None,
    owner: str | None = None,
    service: DashboardService = Depends(_service),
):
    return await service.get_overview(range, account_id, model_id, group_id, gateway, owner)


@router.get("/timeseries", response_model=list[TimeseriesPoint])
async def timeseries(
    range: str = "7d",
    account_id: int | None = None,
    model_id: int | None = None,
    group_id: int | None = None,
    gateway: str | None = None,
    owner: str | None = None,
    service: DashboardService = Depends(_service),
):
    return await service.get_timeseries(range, account_id, model_id, group_id, gateway, owner)


@router.get("/timeseries-by-account", response_model=list[AccountTpmPoint])
async def timeseries_by_account(
    range: str = "7d",
    account_id: int | None = None,
    model_id: int | None = None,
    group_id: int | None = None,
    gateway: str | None = None,
    owner: str | None = None,
    service: DashboardService = Depends(_service),
):
    return await service.get_timeseries_by_account(range, account_id, model_id, group_id, gateway, owner)


@router.get("/by-account", response_model=list[BreakdownItem])
async def by_account(
    range: str = "7d",
    account_id: int | None = None,
    model_id: int | None = None,
    group_id: int | None = None,
    gateway: str | None = None,
    owner: str | None = None,
    service: DashboardService = Depends(_service),
):
    return await service.get_breakdown_by_account(range, model_id, account_id, group_id, gateway, owner)


@router.get("/by-model", response_model=list[BreakdownItem])
async def by_model(
    range: str = "7d",
    account_id: int | None = None,
    model_id: int | None = None,
    group_id: int | None = None,
    gateway: str | None = None,
    owner: str | None = None,
    service: DashboardService = Depends(_service),
):
    return await service.get_breakdown_by_model(range, account_id, group_id, model_id, gateway, owner)


@router.get("/by-deployment", response_model=list[BreakdownItem])
async def by_deployment(
    range: str = "7d",
    account_id: int | None = None,
    group_id: int | None = None,
    owner: str | None = None,
    service: DashboardService = Depends(_service),
):
    return await service.get_breakdown_by_monitored_model(range, account_id, group_id, owner)


@router.get("/fx", response_model=FxRate)
async def fx_rate():
    quote = await usd_to_inr_quote()
    return {"usd_inr": quote["usd_inr"], "base": "USD", "quote": "INR", "source": quote["source"], "is_fallback": quote["is_fallback"]}


@router.get("/export")
async def export_csv(
    range: str = "7d",
    account_id: int | None = None,
    model_id: int | None = None,
    group_id: int | None = None,
    owner: str | None = None,
    service: DashboardService = Depends(_service),
):
    filename, content = await service.export_csv(range, account_id, model_id, group_id, owner)
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
