from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class ProviderCredentials:
    tenant_id: str
    client_id: str
    client_secret: str
    subscription_id: str


@dataclass(frozen=True)
class DiscoveredResource:
    resource_id: str
    name: str
    resource_group: str
    kind: str
    location: str
    endpoint: str


@dataclass(frozen=True)
class DeploymentInfo:
    name: str
    model_name: str
    model_version: str
    sku: str
    capacity: int


@dataclass(frozen=True)
class TokenUsage:
    bucket_start: datetime
    prompt_tokens: int
    cached_tokens: int
    completion_tokens: int
    total_tokens: int
    request_count: int


@dataclass(frozen=True)
class DailyCost:
    usage_date: date
    amount: float
    currency: str


@dataclass(frozen=True)
class CreditBalance:
    remaining: float | None
    used: float | None
    limit: float | None
    currency: str
    unit: str
    label: str
    available: bool


class CloudMetricsProvider(ABC):
    """Read-only contract every cloud provider integration must satisfy.

    New providers (OpenAI, Anthropic, ...) are added by implementing this
    interface and registering them in providers/registry.py - nothing else
    in the app needs to change (Open/Closed).
    """

    @abstractmethod
    async def discover_resources(self, credentials: ProviderCredentials) -> list[DiscoveredResource]: ...

    @abstractmethod
    async def list_deployments(
        self, credentials: ProviderCredentials, resource_id: str
    ) -> list[DeploymentInfo]: ...

    @abstractmethod
    async def get_token_usage(
        self,
        credentials: ProviderCredentials,
        resource_id: str,
        kind: str,
        deployment_name: str,
        start: datetime,
        end: datetime,
    ) -> list[TokenUsage]: ...

    @abstractmethod
    async def get_daily_cost(
        self,
        credentials: ProviderCredentials,
        subscription_id: str,
        resource_id: str,
        start: datetime,
        end: datetime,
    ) -> list[DailyCost]: ...

    @abstractmethod
    async def get_credit_balance(
        self,
        credentials: ProviderCredentials,
        subscription_id: str,
        resource_id: str,
        location: str = "",
        model_names: list[str] | None = None,
        refresh: bool = False,
    ) -> CreditBalance: ...
