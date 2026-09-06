import asyncio

from fastapi import APIRouter, Depends

from app.dependencies import get_current_admin
from app.schemas.system_stats import SystemStats
from app.services.system_stats_service import collector

router = APIRouter(prefix="/api/system", tags=["system"], dependencies=[Depends(get_current_admin)])


@router.get("/stats", response_model=SystemStats)
async def system_stats() -> SystemStats:
    stats = collector.collect()
    if stats.cpu_percent is None:
        await asyncio.sleep(0.15)
        stats = collector.collect()
    return stats
