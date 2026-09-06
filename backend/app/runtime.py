"""Process sizing for the 4 vCPU / 8 GiB host.

Keep a single uvicorn worker: APScheduler and the Telegram poller must not
fork. Concurrency comes from a larger default thread pool (az CLI / ARM) and
asyncio, not extra Python processes.
"""

from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

THREAD_POOL_WORKERS = int(os.environ.get("PORTAL_THREAD_POOL", "16"))
AZ_CLI_CONCURRENCY = int(os.environ.get("PORTAL_AZ_CLI_CONCURRENCY", "12"))
AZURE_SYNC_CONCURRENCY = int(os.environ.get("PORTAL_AZURE_SYNC_CONCURRENCY", "8"))
DEPLOY_JOBS_DEFAULT = int(os.environ.get("PORTAL_DEPLOY_JOBS", "12"))
DEPLOY_JOBS_MAX = int(os.environ.get("PORTAL_DEPLOY_JOBS_MAX", "16"))


def configure_runtime() -> None:
    workers = max(4, THREAD_POOL_WORKERS)
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=workers, thread_name_prefix="portal"))
    formatter = logging.Formatter("%(levelname)s %(name)s: %(message)s")
    for name in ("app.services.telegram_bot", "app.services.telegram_service"):
        telegram_log = logging.getLogger(name)
        telegram_log.setLevel(logging.INFO)
        if not telegram_log.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(formatter)
            telegram_log.addHandler(handler)
        telegram_log.propagate = False
    logger.info(
        "Runtime: thread_pool=%s az_cli=%s azure_sync=%s deploy_jobs=%s/%s",
        workers,
        AZ_CLI_CONCURRENCY,
        AZURE_SYNC_CONCURRENCY,
        DEPLOY_JOBS_DEFAULT,
        DEPLOY_JOBS_MAX,
    )
