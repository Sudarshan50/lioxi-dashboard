from app.providers.azure.provider import AzureProvider
from app.providers.base import CloudMetricsProvider

_PROVIDERS: dict[str, CloudMetricsProvider] = {
    "azure_openai": AzureProvider(),
}


def get_provider(provider_type: str) -> CloudMetricsProvider:
    try:
        return _PROVIDERS[provider_type]
    except KeyError as exc:
        raise ValueError(f"Unsupported provider type: {provider_type}") from exc
