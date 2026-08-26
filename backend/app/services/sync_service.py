import logging
from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.models.provider_account import ProviderAccount
from app.providers.base import CreditBalance, ProviderCredentials
from app.providers.registry import get_provider
from app.repositories.model_repository import ModelRepository
from app.repositories.usage_repository import UsageRepository
from app.services.pricing_service import PricingService

logger = logging.getLogger(__name__)

_UNAVAILABLE_CREDITS = CreditBalance(
    remaining=None,
    used=None,
    limit=None,
    currency="USD",
    unit="currency",
    label="Credits",
    available=False,
)


class SyncService:
    """Pulls token metrics and cost for one account's monitored models and
    upserts them as snapshots. Contains no error handling for a whole account
    failing - that orchestration lives in SyncOrchestrator.
    """

    def __init__(self, model_repository: ModelRepository, usage_repository: UsageRepository) -> None:
        self._model_repository = model_repository
        self._usage_repository = usage_repository

    async def sync_account(self, account: ProviderAccount, client_secret: str) -> None:
        provider = get_provider(account.provider_type)
        credentials = ProviderCredentials(
            tenant_id=account.tenant_id,
            client_id=account.client_id,
            client_secret=client_secret,
            subscription_id=account.subscription_id,
        )
        start, end = self._lookback_window()
        models = await self._model_repository.list_by_account(account.id)

        try:
            balance = await provider.get_credit_balance(
                credentials,
                account.subscription_id,
                account.resource_id,
                location=account.location,
            )
        except Exception:
            logger.warning("Credit lookup failed for account %s", account.name, exc_info=True)
            balance = _UNAVAILABLE_CREDITS
        if balance.available:
            account.credits_remaining = balance.remaining
            account.credits_used = balance.used
            if not account.credits_limit_manual:
                account.credits_limit = balance.limit
                account.credits_currency = balance.currency
                account.credits_unit = balance.unit
                account.credits_label = balance.label
                account.credits_available = True
        elif not account.credits_available and not account.credits_limit_manual:
            account.credits_remaining = balance.remaining
            account.credits_used = balance.used
            account.credits_limit = balance.limit
            account.credits_currency = balance.currency
            account.credits_unit = balance.unit
            account.credits_label = balance.label
            account.credits_available = False

        for model in models:
            if not model.enabled:
                continue
            usage_points = await provider.get_token_usage(
                credentials, account.resource_id, account.kind, model.deployment_name, start, end
            )
            pricing = model.registered_model
            for point in usage_points:
                cost = PricingService.estimate_cost(
                    point.prompt_tokens,
                    point.cached_tokens,
                    point.completion_tokens,
                    pricing.input_price_per_million,
                    pricing.cached_input_price_per_million,
                    pricing.output_price_per_million,
                )
                await self._usage_repository.upsert_snapshot(
                    provider_account_id=account.id,
                    monitored_model_id=model.id,
                    bucket_start=point.bucket_start,
                    prompt_tokens=point.prompt_tokens,
                    cached_tokens=point.cached_tokens,
                    completion_tokens=point.completion_tokens,
                    total_tokens=point.total_tokens,
                    request_count=point.request_count,
                    estimated_cost_usd=cost,
                )

        daily_costs = await provider.get_daily_cost(credentials, account.subscription_id, account.resource_id, start, end)
        for daily in daily_costs:
            await self._usage_repository.upsert_cost_snapshot(
                provider_account_id=account.id,
                usage_date=daily.usage_date,
                actual_cost_usd=daily.amount,
                currency=daily.currency,
            )

    @staticmethod
    def _lookback_window() -> tuple[datetime, datetime]:
        """Azure Monitor anchors PT1H buckets to the start of the requested
        timespan rather than to wall-clock hour boundaries. If `end` carried
        live minute/second precision, every sync run (executed at a different
        moment) would produce a different set of bucket timestamps for the
        same real hours, so upserts would never match and usage would be
        double/triple/N-counted across runs. Flooring to the current hour
        keeps bucket boundaries identical across every sync, making upserts
        properly idempotent.
        """
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        end = now + timedelta(hours=1)
        start = end - timedelta(hours=get_settings().sync_lookback_hours)
        return start, end
