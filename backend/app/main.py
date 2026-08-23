import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import SessionLocal, init_models
from app.dependencies import get_sync_orchestrator
from app.repositories.admin_repository import AdminRepository
from app.routers import account_groups, accounts, alerts, auth, dashboard, models, registered_models
from app.services.bootstrap import ensure_admin_seeded
from app.services.telegram_bot import run_bot_polling

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    await init_models()

    async with SessionLocal() as session:
        await ensure_admin_seeded(AdminRepository(session), settings.admin_username, settings.admin_password)

    orchestrator = get_sync_orchestrator()
    scheduler.add_job(
        orchestrator.sync_all,
        "interval",
        minutes=settings.sync_interval_minutes,
        next_run_time=datetime.now(),
        id="sync_all_accounts",
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


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}
