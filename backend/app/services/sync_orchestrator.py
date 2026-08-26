import asyncio
import logging
from datetime import datetime, timezone

from app.core.crypto import SecretBox
from app.database import SessionLocal
from app.repositories.account_repository import AccountRepository
from app.repositories.model_repository import ModelRepository
from app.repositories.usage_repository import UsageRepository
from app.services.alert_service import check_new_api_credit_alerts
from app.services.new_api_service import sync_new_api
from app.services.sync_service import SyncService

logger = logging.getLogger(__name__)


class SyncOrchestrator:
    """Runs SyncService per account, isolating failures so one bad account
    (bad credentials, revoked role, rate limit) never blocks the others.
    """

    def __init__(self, secret_box: SecretBox) -> None:
        self._secret_box = secret_box
        self._newapi_lock = asyncio.Lock()
        self._azure_lock = asyncio.Lock()

    async def sync_all(self) -> dict:
        """Manual Sync all: NewAPI + alerts, then a slow Azure sweep."""
        newapi = await self.sync_new_api_cycle()
        azure = await self.sync_azure_all()
        return {**azure, "newapi": newapi}

    async def sync_new_api_cycle(self) -> dict:
        """Spend, channel status, then credit alerts. Independent of Azure."""
        async with self._newapi_lock:
            result = await self.sync_new_api_safe()
            result["alerts"] = await self.check_alerts_safe()
            return result

    async def sync_azure_all(self) -> dict:
        async with self._azure_lock:
            async with SessionLocal() as session:
                accounts = await AccountRepository(session).list_all()
            limit = asyncio.Semaphore(3)

            async def _bounded(account_id: int) -> dict:
                async with limit:
                    return await self.sync_one(account_id)

            results = await asyncio.gather(*[_bounded(account.id) for account in accounts])
            failed = [result for result in results if result.get("status") == "error"]
            return {
                "status": "completed" if not failed else "partial",
                "synced": len(results) - len(failed),
                "failed": [{"id": result["id"], "name": result["name"], "error": result.get("error")} for result in failed],
            }

    async def sync_new_api_safe(self) -> dict:
        """NewAPI gateway sync must never block the Azure sync path."""
        try:
            async with SessionLocal() as session:
                return await sync_new_api(session)
        except Exception as exc:  # noqa: BLE001 - degraded, not fatal
            logger.warning("NewAPI sync failed", exc_info=True)
            return {"status": "error", "error": str(exc)[:300]}

    async def check_alerts_safe(self) -> dict:
        try:
            async with SessionLocal() as session:
                return await check_new_api_credit_alerts(session)
        except Exception:  # noqa: BLE001 - alerting must not fail the sync
            logger.warning("Credit alert check failed", exc_info=True)
            return {"status": "error"}

    async def sync_one(self, account_id: int) -> dict:
        async with SessionLocal() as session:
            account_repository = AccountRepository(session)
            account = await account_repository.get(account_id)
            if account is None:
                return {"id": account_id, "name": None, "status": "missing", "error": "Account not found"}

            sync_service = SyncService(ModelRepository(session), UsageRepository(session))
            try:
                secret = self._secret_box.decrypt(account.client_secret_encrypted)
                await sync_service.sync_account(account, secret)
                account.last_sync_status = "success"
                account.last_sync_error = None
            except Exception as exc:  # noqa: BLE001 - surfaced to the admin UI, not raised further
                logger.warning("Sync failed for account %s", account.name, exc_info=True)
                account.last_sync_status = "error"
                account.last_sync_error = str(exc)[:500]

            account.last_synced_at = datetime.now(timezone.utc)
            await account_repository.save(account)
            return {
                "id": account.id,
                "name": account.name,
                "status": account.last_sync_status,
                "error": account.last_sync_error,
            }
