from datetime import datetime

from app.providers.azure.arm_client import AzureArmClient
from app.providers.azure.cost import AzureCostService
from app.providers.azure.credits import AzureCreditService
from app.providers.azure.discovery import AzureDiscoveryService
from app.providers.azure.metrics import AzureMetricsService
from app.providers.azure.token_provider import AzureTokenProvider
from app.providers.base import (
    CloudMetricsProvider,
    CreditBalance,
    DailyCost,
    DeploymentInfo,
    DiscoveredResource,
    ProviderCredentials,
    TokenUsage,
)


class AzureProvider(CloudMetricsProvider):
    def __init__(self) -> None:
        arm_client = AzureArmClient(AzureTokenProvider())
        self._discovery = AzureDiscoveryService(arm_client)
        self._metrics = AzureMetricsService(arm_client)
        self._cost = AzureCostService(arm_client)
        self._credits = AzureCreditService(arm_client)

    async def discover_resources(self, credentials: ProviderCredentials) -> list[DiscoveredResource]:
        return await self._discovery.list_accounts(credentials)

    async def list_deployments(self, credentials: ProviderCredentials, resource_id: str) -> list[DeploymentInfo]:
        return await self._discovery.list_deployments(credentials, resource_id)

    async def get_token_usage(
        self,
        credentials: ProviderCredentials,
        resource_id: str,
        kind: str,
        deployment_name: str,
        start: datetime,
        end: datetime,
    ) -> list[TokenUsage]:
        return await self._metrics.get_token_usage(credentials, resource_id, kind, deployment_name, start, end)

    async def get_daily_cost(
        self,
        credentials: ProviderCredentials,
        subscription_id: str,
        resource_id: str,
        start: datetime,
        end: datetime,
    ) -> list[DailyCost]:
        return await self._cost.get_daily_cost(credentials, subscription_id, resource_id, start, end)

    async def get_credit_balance(
        self,
        credentials: ProviderCredentials,
        subscription_id: str,
        resource_id: str,
        location: str = "",
        model_names: list[str] | None = None,
        refresh: bool = False,
    ) -> CreditBalance:
        return await self._credits.get_credit_balance(
            credentials,
            subscription_id,
            resource_id,
            location=location,
            model_names=model_names,
            refresh=refresh,
        )
