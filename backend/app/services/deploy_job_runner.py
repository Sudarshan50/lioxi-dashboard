"""In-process Kimi deploy jobs. The HTTP request only enqueues; the browser can leave."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from app.runtime import AZ_CLI_CONCURRENCY, DEPLOY_JOBS_MAX
from app.schemas.kimi_deploy import KimiDeployJobSnapshot
from app.services.kimi_deploy_service import KimiDeployError, deploy_accounts

logger = logging.getLogger(__name__)

# Shared Azure-deploy slots for join-approve jobs and the Deploy K3 page job.
_SLOT_COUNT = max(1, min(DEPLOY_JOBS_MAX, AZ_CLI_CONCURRENCY))
_SLOTS = asyncio.Semaphore(_SLOT_COUNT)
_guard = asyncio.Lock()
_job: dict[str, Any] | None = None
_task: asyncio.Task | None = None


class _SlotHold:
    def __init__(self, count: int) -> None:
        self.count = max(1, count)
        self._got = 0

    async def __aenter__(self) -> "_SlotHold":
        try:
            for _ in range(self.count):
                await _SLOTS.acquire()
                self._got += 1
        except BaseException:
            self._release()
            raise
        return self

    async def __aexit__(self, *_exc: object) -> None:
        self._release()

    def _release(self) -> None:
        while self._got:
            _SLOTS.release()
            self._got -= 1


def deploy_slots(count: int = 1) -> _SlotHold:
    return _SlotHold(count)


def kimi_job_running() -> bool:
    return bool(_job and _job.get("running"))


def kimi_job_snapshot() -> KimiDeployJobSnapshot:
    current = _job or {}
    results = current.get("results")
    return KimiDeployJobSnapshot(
        running=bool(current.get("running")),
        job_id=current.get("job_id"),
        total=int(current.get("total") or 0),
        error=current.get("error"),
        results=results,
    )


async def start_kimi_deploy_job(
    accounts: list[dict[str, Any]],
    jobs: int,
    new_api_priority: int,
    new_api_weight: int,
) -> KimiDeployJobSnapshot:
    global _job, _task
    async with _guard:
        if kimi_job_running():
            raise KimiDeployError("A Kimi deploy is already running on the server.")
        job_id = str(uuid.uuid4())
        _job = {
            "running": True,
            "job_id": job_id,
            "total": len(accounts),
            "error": None,
            "results": None,
        }
        _task = asyncio.create_task(
            _run_kimi_deploy_job(job_id, accounts, jobs, new_api_priority, new_api_weight)
        )
    logger.info("Kimi deploy job %s queued for %s account(s)", job_id, len(accounts))
    return kimi_job_snapshot()


async def _run_kimi_deploy_job(
    job_id: str,
    accounts: list[dict[str, Any]],
    jobs: int,
    new_api_priority: int,
    new_api_weight: int,
) -> None:
    from app.database import SessionLocal

    global _job
    workers = min(max(1, jobs), max(1, len(accounts)), _SLOT_COUNT)
    try:
        async with deploy_slots(workers):
            async with SessionLocal() as db:
                results = await deploy_accounts(
                    accounts,
                    workers,
                    session=db,
                    new_api_priority=new_api_priority,
                    new_api_weight=new_api_weight,
                )
        if _job and _job.get("job_id") == job_id:
            _job["results"] = results
            _job["error"] = None
    except Exception as exc:  # noqa: BLE001
        logger.exception("Kimi deploy job %s failed", job_id)
        if _job and _job.get("job_id") == job_id:
            _job["error"] = str(exc)[:800]
            _job["results"] = None
    finally:
        if _job and _job.get("job_id") == job_id:
            _job["running"] = False
