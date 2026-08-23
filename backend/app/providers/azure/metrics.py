from datetime import datetime, timezone

from app.providers.azure.arm_client import AzureArmClient
from app.providers.base import ProviderCredentials, TokenUsage

_METRICS_API = "2023-10-01"

# Foundry-style accounts (kind=AIServices, e.g. Fireworks/Kimi deployments) emit these.
_FOUNDRY_METRIC_NAMES = "InputTokens,OutputTokens,TotalTokens,ModelRequests,cacheReadInputTokens"
# Classic Azure OpenAI accounts (kind=OpenAI) emit these instead.
_LEGACY_METRIC_NAMES = "ProcessedPromptTokens,GeneratedTokens,TokenTransaction,AzureOpenAIRequests"


class AzureMetricsService:
    """Reads Azure Monitor platform metrics. Never invokes the model itself."""

    def __init__(self, arm_client: AzureArmClient) -> None:
        self._arm_client = arm_client

    async def get_token_usage(
        self,
        credentials: ProviderCredentials,
        resource_id: str,
        kind: str,
        deployment_name: str,
        start: datetime,
        end: datetime,
    ) -> list[TokenUsage]:
        metric_names = _FOUNDRY_METRIC_NAMES if kind == "AIServices" else _LEGACY_METRIC_NAMES
        params = {
            "api-version": _METRICS_API,
            "timespan": f"{_iso(start)}/{_iso(end)}",
            "interval": "PT1H",
            "metricnames": metric_names,
            "aggregation": "Total",
            "$filter": f"ModelDeploymentName eq '{deployment_name}'",
        }
        body = await self._arm_client.get(
            credentials, f"{resource_id}/providers/Microsoft.Insights/metrics", params=params
        )
        return _to_token_usage(body, kind)


def _iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_token_usage(body: dict, kind: str) -> list[TokenUsage]:
    buckets: dict[str, dict[str, float]] = {}
    for metric in body.get("value", []):
        name = metric.get("name", {}).get("value", "")
        for series in metric.get("timeseries", []):
            for point in series.get("data", []):
                total = point.get("total")
                if total is None:
                    continue
                bucket = buckets.setdefault(point["timeStamp"], {})
                bucket[name] = bucket.get(name, 0) + total

    usage: list[TokenUsage] = []
    for timestamp, values in sorted(buckets.items()):
        if kind == "AIServices":
            prompt = values.get("InputTokens", 0)
            completion = values.get("OutputTokens", 0)
            total = values.get("TotalTokens", prompt + completion)
            cached = values.get("cacheReadInputTokens", 0)
            requests = values.get("ModelRequests", 0)
        else:
            prompt = values.get("ProcessedPromptTokens", 0)
            completion = values.get("GeneratedTokens", 0)
            total = values.get("TokenTransaction", prompt + completion)
            cached = 0
            requests = values.get("AzureOpenAIRequests", 0)

        if prompt == 0 and completion == 0 and total == 0 and requests == 0:
            continue

        usage.append(
            TokenUsage(
                bucket_start=datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc),
                prompt_tokens=int(prompt),
                cached_tokens=int(cached),
                completion_tokens=int(completion),
                total_tokens=int(total),
                request_count=int(requests),
            )
        )
    return usage
