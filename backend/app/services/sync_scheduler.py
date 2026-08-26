"""APScheduler handles for NewAPI (fast) and Azure (slow) jobs."""

from apscheduler.schedulers.asyncio import AsyncIOScheduler

NEWAPI_JOB_ID = "sync_new_api"
AZURE_JOB_ID = "sync_azure_accounts"
# Back-compat alias used by older imports.
JOB_ID = NEWAPI_JOB_ID

_scheduler: AsyncIOScheduler | None = None


def bind_scheduler(scheduler: AsyncIOScheduler) -> None:
    global _scheduler
    _scheduler = scheduler


def _reschedule(job_id: str, minutes: int) -> int:
    if _scheduler is None:
        return minutes
    job = _scheduler.get_job(job_id)
    if job is None:
        return minutes
    _scheduler.reschedule_job(job_id, trigger="interval", minutes=minutes)
    return minutes


def apply_sync_interval(minutes: int) -> int:
    """Reschedule the NewAPI + alerts job."""
    return _reschedule(NEWAPI_JOB_ID, minutes)


def apply_azure_sync_interval(minutes: int) -> int:
    """Reschedule the slow Azure token/cost job."""
    return _reschedule(AZURE_JOB_ID, minutes)
