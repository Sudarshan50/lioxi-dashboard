from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AccountDiscoverRequest(BaseModel):
    tenant_id: str
    client_id: str
    client_secret: str
    subscription_id: str


class AccountDiscoverDeploymentsRequest(AccountDiscoverRequest):
    resource_id: str


class AccountResourceDeploymentsRequest(BaseModel):
    resource_id: str


class DiscoveredResourceResponse(BaseModel):
    resource_id: str
    name: str
    resource_group: str
    kind: str
    location: str
    endpoint: str


class AccountCreateRequest(BaseModel):
    name: str
    tenant_id: str
    client_id: str
    client_secret: str
    subscription_id: str
    resource_name: str
    resource_id: str = ""
    resource_group: str = ""
    endpoint: str = ""
    kind: str = ""
    location: str = ""
    credits_limit: float | None = None
    owner_tag: str | None = None


class AccountUpdateRequest(BaseModel):
    name: str | None = None
    resource_id: str | None = None
    resource_group: str | None = None
    resource_name: str | None = None
    endpoint: str | None = None
    kind: str | None = None
    location: str | None = None
    credits_limit: float | None = None
    credits_limit_manual: bool | None = None
    owner_tag: str | None = None


class AccountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    provider_type: str
    subscription_id: str
    resource_id: str
    resource_group: str
    resource_name: str
    endpoint: str
    kind: str
    location: str
    last_synced_at: datetime | None
    last_sync_status: str | None
    last_sync_error: str | None
    credits_remaining: float | None
    credits_used: float | None
    credits_limit: float | None
    credits_currency: str | None
    credits_unit: str | None
    credits_label: str | None
    credits_available: bool
    credits_limit_manual: bool = False
    new_api_gateway: str | None = None
    new_api_channel_id: int | None = None
    new_api_name: str | None = None
    new_api_tag: str | None = None
    owner_tag: str | None = None
    new_api_used_quota: float | None = None
    new_api_cost_o1_usd: float | None = None
    new_api_cost_o2_usd: float | None = None
    new_api_cost_usd: float | None = None
    new_api_status: int | None = None
    new_api_status_o1: int | None = None
    new_api_status_o2: int | None = None
    new_api_weight: int | None = None
    new_api_priority: int | None = None
    new_api_synced_at: datetime | None = None
    created_at: datetime
    payable_settled: bool = False
    payable_settled_at: datetime | None = None
    at_cap_manual: bool = False


class DeploymentResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    name: str
    model_name: str
    model_version: str
    sku: str
    capacity: int


class SyncFailure(BaseModel):
    id: int
    name: str | None
    error: str | None


class SyncAllResponse(BaseModel):
    status: str
    synced: int
    failed: list[SyncFailure]
