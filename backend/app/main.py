import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import SessionLocal, init_models
from app.dependencies import get_sync_orchestrator
from app.repositories.admin_repository import AdminRepository
from app.routers import account_groups, accounts, alerts, auth, dashboard, kimi_deploy, models, pending, registered_models, submit
from app.services.alert_service import (
    DEFAULT_AZURE_SYNC_INTERVAL_MINUTES,
    DEFAULT_SYNC_INTERVAL_MINUTES,
    get_alert_config,
)
from app.services.bootstrap import ensure_admin_seeded
from app.services.owner_tag import apply_owner_tags
from app.services.sync_scheduler import AZURE_JOB_ID, NEWAPI_JOB_ID, bind_scheduler
from app.services.telegram_bot import run_bot_polling

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    await init_models()

    async with SessionLocal() as session:
        await ensure_admin_seeded(AdminRepository(session), settings.admin_username, settings.admin_password)
        await apply_owner_tags(session)
        from app.services.submit_service import expire_stale

        await expire_stale(session)
        config = await get_alert_config(session)
    from app.services.az_cli_session import cleanup_stale_config_dirs

    cleanup_stale_config_dirs()
    newapi_minutes = int(config.get("sync_interval_minutes") or DEFAULT_SYNC_INTERVAL_MINUTES)
    azure_minutes = int(
        config.get("azure_sync_interval_minutes")
        or settings.azure_sync_interval_minutes
        or DEFAULT_AZURE_SYNC_INTERVAL_MINUTES
    )

    orchestrator = get_sync_orchestrator()
    bind_scheduler(scheduler)
    scheduler.add_job(
        orchestrator.sync_new_api_cycle,
        "interval",
        minutes=newapi_minutes,
        next_run_time=datetime.now(),
        id=NEWAPI_JOB_ID,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        orchestrator.sync_azure_all,
        "interval",
        minutes=azure_minutes,
        next_run_time=datetime.now() + timedelta(minutes=5),
        id=AZURE_JOB_ID,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()

    bot_task = asyncio.create_task(run_bot_polling())
    try:
        yield
    finally:
        bot_task.cancel()
        with suppress(asyncio.CancelledError):
            await bot_task
        scheduler.shutdown(wait=False)
        from app.services.azure_inventory_cache import close_azure_inventory_cache
        from app.services.az_cli_session import cleanup_stale_config_dirs

        await close_azure_inventory_cache()
        cleanup_stale_config_dirs()


app = FastAPI(title="LLM Usage Monitoring Portal", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(accounts.router)
app.include_router(alerts.router)
app.include_router(account_groups.router)
app.include_router(registered_models.router)
app.include_router(models.router)
app.include_router(dashboard.router)
app.include_router(kimi_deploy.router)
app.include_router(submit.router)
app.include_router(pending.router)


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}
