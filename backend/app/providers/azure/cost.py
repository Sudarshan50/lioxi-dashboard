import logging
from datetime import date, datetime

from app.providers.azure.arm_client import AzureArmClient
from app.providers.base import DailyCost, ProviderCredentials

logger = logging.getLogger(__name__)

_COST_API = "2024-08-01"


class AzureCostService:
    """Reads actual billed cost from Azure Cost Management. Best-effort: some
    subscription types (e.g. sponsorships) restrict this API, so failures are
    logged and degrade to an empty result rather than breaking the sync.
    """

    def __init__(self, arm_client: AzureArmClient) -> None:
        self._arm_client = arm_client

    async def get_daily_cost(
        self,
        credentials: ProviderCredentials,
        subscription_id: str,
        resource_id: str,
        start: datetime,
        end: datetime,
    ) -> list[DailyCost]:
        path = f"/subscriptions/{subscription_id}/providers/Microsoft.CostManagement/query"
        payload = {
            "type": "ActualCost",
            "timeframe": "Custom",
            "timePeriod": {"from": _iso(start), "to": _iso(end)},
            "dataset": {
                "granularity": "Daily",
                "aggregation": {"totalCost": {"name": "PreTaxCost", "function": "Sum"}},
                "filter": {"dimensions": {"name": "ResourceId", "operator": "In", "values": [resource_id]}},
            },
        }
        try:
            body = await self._arm_client.post(credentials, path, json=payload, params={"api-version": _COST_API})
        except Exception:
            logger.warning("Cost Management query failed for %s", resource_id, exc_info=True)
            return []
        return _to_daily_cost(body)


def _iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_daily_cost(body: dict) -> list[DailyCost]:
    properties = body.get("properties", {})
    columns = [c.get("name") for c in properties.get("columns", [])]
    if "PreTaxCost" not in columns:
        return []

    cost_idx = columns.index("PreTaxCost")
    date_idx = columns.index("UsageDate") if "UsageDate" in columns else None
    currency_idx = columns.index("Currency") if "Currency" in columns else None

    results = []
    for row in properties.get("rows", []):
        results.append(
            DailyCost(
                usage_date=_parse_date(row[date_idx]) if date_idx is not None else date.today(),
                amount=float(row[cost_idx]),
                currency=row[currency_idx] if currency_idx is not None else "USD",
            )
        )
    return results


def _parse_date(value: object) -> date:
    return datetime.strptime(str(value), "%Y%m%d").date()
