import asyncio
import logging
import time
from datetime import datetime, timezone
from urllib.parse import quote

from app.providers.azure.arm_client import AzureArmClient
from app.providers.base import CreditBalance, ProviderCredentials

logger = logging.getLogger(__name__)

_BILLING_API = "2024-04-01"
_CONSUMPTION_API = "2024-08-01"
_CONSUMPTION_CREDIT_API = "2023-03-01"
_CONSUMPTION_BALANCES_API = "2021-10-01"
_CACHE_TTL_SECONDS = 120.0
_INACTIVE_LOT_STATUSES = {"expired", "inactive", "closed"}

_UNAVAILABLE = CreditBalance(
    remaining=None,
    used=None,
    limit=None,
    currency="USD",
    unit="currency",
    label="Credits",
    available=False,
)


class AzureCreditService:
    """Reads remaining Azure monetary credits from billing and consumption APIs."""

    def __init__(self, arm_client: AzureArmClient) -> None:
        self._arm_client = arm_client
        self._subscription_cache: dict[tuple[str, str, str], tuple[float, CreditBalance]] = {}
        self._subscription_locks: dict[tuple[str, str, str], asyncio.Lock] = {}

    async def get_credit_balance(
        self,
        credentials: ProviderCredentials,
        subscription_id: str,
        resource_id: str,  # noqa: ARG002 - kept for provider interface
        location: str = "",
        model_names: list[str] | None = None,
        refresh: bool = False,
    ) -> CreditBalance:
        return await self.get_subscription_credits(credentials, subscription_id, refresh=refresh)

    async def get_subscription_credits(
        self, credentials: ProviderCredentials, subscription_id: str, refresh: bool = False
    ) -> CreditBalance:
        key = (credentials.tenant_id, credentials.client_id, subscription_id)
        if refresh:
            self._subscription_cache.pop(key, None)
        now = time.monotonic()
        cached = self._subscription_cache.get(key)
        if cached and cached[0] > now:
            return cached[1]
        lock = self._subscription_locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self._subscription_cache.get(key)
            if cached and cached[0] > time.monotonic():
                return cached[1]
            balance = await self._load_subscription_credits(credentials, subscription_id)
            if balance.available:
                self._subscription_cache[key] = (time.monotonic() + _CACHE_TTL_SECONDS, balance)
            return balance

    async def _load_subscription_credits(
        self, credentials: ProviderCredentials, subscription_id: str
    ) -> CreditBalance:
        for loader in (
            self._from_billing_profiles,
            self._from_billing_lots,
            self._from_subscription_credits,
            self._from_subscription_balances,
        ):
            try:
                balance = await loader(credentials, subscription_id)
            except Exception as exc:
                message = str(exc)
                if "404" in message or "403" in message:
                    logger.info("Credit lookup %s not supported: %s", loader.__name__, exc)
                else:
                    logger.warning("Credit lookup failed via %s", loader.__name__, exc_info=True)
                continue
            if balance.available:
                return balance
        return _UNAVAILABLE

    async def _from_billing_profiles(self, credentials: ProviderCredentials, _subscription_id: str) -> CreditBalance:
        accounts = await self._arm_client.get_all_pages(
            credentials, "/providers/Microsoft.Billing/billingAccounts", params={"api-version": _BILLING_API}
        )
        now = datetime.now(timezone.utc)
        remaining = 0.0
        limit = 0.0
        pending = 0.0
        currency = None
        labels: list[str] = []
        found = False
        for account in accounts:
            account_id = account.get("name") or account.get("id", "").rsplit("/", 1)[-1]
            if not account_id:
                continue
            encoded_account = quote(account_id, safe="")
            try:
                profiles = await self._arm_client.get_all_pages(
                    credentials,
                    f"/providers/Microsoft.Billing/billingAccounts/{encoded_account}/billingProfiles",
                    params={"api-version": _BILLING_API},
                )
            except Exception:
                logger.warning("Could not list billing profiles for %s", account_id, exc_info=True)
                continue
            for profile in profiles:
                lot_original, lot_currency, lot_label, summary = await self._credits_for_profile(
                    credentials, encoded_account, profile, now
                )
                if lot_original is None and summary is None:
                    continue
                found = True
                if lot_original is not None:
                    limit += lot_original
                    currency = lot_currency or currency
                if lot_label:
                    labels.append(lot_label)
                if summary is not None:
                    estimated, current, pending_charges, summary_currency = summary
                    remaining += estimated if estimated is not None else current or 0.0
                    if lot_original is None and current is not None:
                        limit += current
                    if pending_charges is not None:
                        pending += abs(pending_charges)
                    currency = summary_currency or currency
                elif lot_original is not None:
                    remaining += lot_original
        if not found:
            return _UNAVAILABLE
        if pending <= 0 and limit > 0:
            pending = max(limit - remaining, 0.0)
        return CreditBalance(
            remaining=remaining,
            used=pending or None,
            limit=limit or None,
            currency=currency or "USD",
            unit="currency",
            label=labels[0] if len(set(labels)) == 1 else "Credits",
            available=True,
        )

    async def _credits_for_profile(
        self, credentials: ProviderCredentials, encoded_account: str, profile: dict, now: datetime
    ) -> tuple[float | None, str | None, str | None, tuple[float | None, float | None, float | None, str | None] | None]:
        properties = profile.get("properties") or {}
        identifiers = []
        for candidate in (properties.get("systemId"), profile.get("name"), (profile.get("id") or "").rsplit("/", 1)[-1]):
            if candidate and str(candidate) not in identifiers:
                identifiers.append(str(candidate))
        lot_original = lot_currency = lot_label = None
        summary = None
        for profile_id in identifiers:
            profile_path = (
                f"/providers/Microsoft.Billing/billingAccounts/{encoded_account}"
                f"/billingProfiles/{quote(profile_id, safe='')}/providers/Microsoft.Consumption"
            )
            if lot_original is None:
                lot_original, lot_currency, lot_label = await self._profile_lot_totals(credentials, profile_path, now)
            if summary is None:
                summary = await self._profile_balance_summary(credentials, profile_path)
            if lot_original is not None and summary is not None:
                break
        return lot_original, lot_currency, lot_label, summary

    async def _profile_lot_totals(
        self, credentials: ProviderCredentials, profile_path: str, now: datetime
    ) -> tuple[float | None, str | None, str | None]:
        try:
            lots = await self._arm_client.get_all_pages(
                credentials, f"{profile_path}/lots", params={"api-version": _CONSUMPTION_CREDIT_API}
            )
        except Exception:
            logger.info("No credit lots for %s", profile_path)
            return None, None, None
        original = 0.0
        currency = None
        label = None
        found = False
        for lot in lots:
            properties = lot.get("properties", {})
            if _lot_inactive(properties, now):
                continue
            amount = _amount_value(properties.get("originalAmount"))
            if amount is None:
                continue
            found = True
            original += amount
            currency = _amount_currency(properties.get("originalAmount")) or properties.get("creditCurrency") or currency
            source = properties.get("source")
            if source:
                label = str(source)
        if not found:
            return None, None, None
        return original, currency, label

    async def _profile_balance_summary(
        self, credentials: ProviderCredentials, profile_path: str
    ) -> tuple[float | None, float | None, float | None, str | None] | None:
        try:
            body = await self._arm_client.get(
                credentials, f"{profile_path}/credits/balanceSummary", params={"api-version": _CONSUMPTION_CREDIT_API}
            )
        except Exception:
            logger.info("No credit balance summary for %s", profile_path)
            return None
        properties = body.get("properties", body)
        summary = properties.get("balanceSummary") or properties
        estimated = _amount_value(summary.get("estimatedBalance"))
        current = _amount_value(summary.get("currentBalance"))
        pending = _amount_value(properties.get("pendingEligibleCharges"))
        currency = (
            _amount_currency(summary.get("estimatedBalance"))
            or _amount_currency(summary.get("currentBalance"))
            or properties.get("creditCurrency")
        )
        if estimated is None and current is None:
            return None
        return estimated, current, pending, currency

    async def _from_billing_lots(self, credentials: ProviderCredentials, _subscription_id: str) -> CreditBalance:
        accounts = await self._arm_client.get_all_pages(
            credentials, "/providers/Microsoft.Billing/billingAccounts", params={"api-version": _BILLING_API}
        )
        remaining = 0.0
        currency = "USD"
        found = False
        now = datetime.now(timezone.utc)
        for account in accounts:
            account_id = account.get("name") or account.get("id", "").rsplit("/", 1)[-1]
            if not account_id:
                continue
            try:
                lots = await self._arm_client.get_all_pages(
                    credentials,
                    f"/providers/Microsoft.Billing/billingAccounts/{quote(account_id, safe='')}/providers/Microsoft.Consumption/lots",
                    params={"api-version": _CONSUMPTION_API},
                )
            except Exception:
                logger.info("No account-level credit lots for billing account %s", account_id)
                continue
            for lot in lots:
                properties = lot.get("properties", {})
                if _lot_inactive(properties, now):
                    continue
                value, lot_currency = _lot_remaining(properties)
                if value is None:
                    continue
                found = True
                remaining += value
                currency = lot_currency or currency
        if not found:
            return _UNAVAILABLE
        return CreditBalance(
            remaining=remaining,
            used=None,
            limit=None,
            currency=currency,
            unit="currency",
            label="Credits",
            available=True,
        )

    async def _from_subscription_credits(
        self, credentials: ProviderCredentials, subscription_id: str
    ) -> CreditBalance:
        body = await self._arm_client.get(
            credentials,
            f"/subscriptions/{subscription_id}/providers/Microsoft.Consumption/credits",
            params={"api-version": "2023-05-01"},
        )
        items = body.get("value")
        if items is None:
            items = [body] if body.get("properties") else []
        remaining = 0.0
        currency = "USD"
        found = False
        for item in items:
            properties = item.get("properties", item)
            summary = properties.get("balanceSummary") or properties
            current = summary.get("currentBalance") or summary.get("estimatedBalance")
            value = _amount_value(current)
            if value is None:
                value = _numeric(properties.get("balance", properties.get("remainingBalance")))
            if value is None:
                continue
            found = True
            remaining += value
            currency = (
                _amount_currency(current)
                or properties.get("creditCurrency")
                or properties.get("billingCurrency")
                or currency
            )
        if not found:
            return _UNAVAILABLE
        return CreditBalance(
            remaining=remaining,
            used=None,
            limit=None,
            currency=currency,
            unit="currency",
            label="Credits",
            available=True,
        )

    async def _from_subscription_balances(
        self, credentials: ProviderCredentials, subscription_id: str
    ) -> CreditBalance:
        body = await self._arm_client.get(
            credentials,
            f"/subscriptions/{subscription_id}/providers/Microsoft.Consumption/balances",
            params={"api-version": _CONSUMPTION_BALANCES_API},
        )
        properties = body.get("properties", body)
        remaining_raw = properties.get("endingBalance", properties.get("availableBalance"))
        remaining = _amount_value(remaining_raw)
        if remaining is None:
            return _UNAVAILABLE
        return CreditBalance(
            remaining=remaining,
            used=_amount_value(properties.get("used", properties.get("newCharges"))),
            limit=_amount_value(properties.get("beginningBalance")),
            currency=_amount_currency(remaining_raw) or properties.get("currency") or "USD",
            unit="currency",
            label="Credits",
            available=True,
        )


def _lot_remaining(properties: dict) -> tuple[float | None, str | None]:
    original = properties.get("originalAmount")
    used = properties.get("usedAmount")
    original_value = _amount_value(original)
    used_value = _amount_value(used)
    if original_value is not None and used_value is not None:
        return max(original_value - used_value, 0.0), _amount_currency(original) or _amount_currency(used)
    closed = properties.get("closedBalance")
    closed_value = _amount_value(closed)
    if closed_value is None:
        return None, None
    return closed_value, _amount_currency(closed) or properties.get("creditCurrency") or properties.get("billingCurrency")


def _amount_value(value: object) -> float | None:
    if isinstance(value, dict):
        return _numeric(value.get("value"))
    return _numeric(value)


def _amount_currency(value: object) -> str | None:
    if isinstance(value, dict):
        currency = value.get("currency")
        return str(currency) if currency else None
    return None


def _numeric(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _lot_inactive(properties: dict, now: datetime) -> bool:
    status = str(properties.get("status") or "").lower()
    if status in _INACTIVE_LOT_STATUSES:
        return True
    return _lot_expired(properties, now)


def _lot_expired(properties: dict, now: datetime) -> bool:
    raw = properties.get("expirationDate")
    if not raw:
        return False
    parsed = _parse_date(str(raw))
    return parsed is not None and parsed < now


def _parse_date(value: str) -> datetime | None:
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ", "%m/%d/%Y %H:%M:%S", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None
