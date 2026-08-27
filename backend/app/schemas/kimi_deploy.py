from typing import Any

from pydantic import BaseModel, Field, model_validator


class KimiDeployRequest(BaseModel):
    accounts: list[dict[str, Any]] = Field(min_length=1)
    jobs: int = Field(default=32, ge=1, le=64)
    new_api_priority: int = Field(default=13, ge=0, le=10000)
    new_api_weight: int = Field(default=1, ge=1, le=10000)


class KimiCreditsRequest(BaseModel):
    accounts: list[dict[str, Any]] = Field(min_length=1)


class KimiBootstrapRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    email: str = ""


class KimiRegenerateRequest(BaseModel):
    accounts: list[dict[str, Any]] = Field(min_length=1)
    jobs: int = Field(default=32, ge=1, le=64)


class KimiCreditSnapshot(BaseModel):
    ok: bool
    name: str | None = None
    subscription_id: str | None = None
    subscription_name: str | None = None
    credits_limit: float | None = None
    credits_remaining: float | None = None
    credits_used: float | None = None
    credits_currency: str | None = None
    credits_label: str | None = None
    credits_available: bool = False
    error: str | None = None


class KimiCreditsResponse(BaseModel):
    results: list[KimiCreditSnapshot]


class KimiDeployResult(BaseModel):
    ok: bool
    name: str | None = None
    email: str | None = None
    azure_openai_endpoint: str | None = None
    deployment_name: str | None = None
    model: str | None = None
    sku: str | None = None
    tpm: int | None = None
    rpm: int | None = None
    capacity: int | None = None
    quota_limit: int | None = None
    region: str | None = None
    account_name: str | None = None
    resource_group: str | None = None
    subscription_id: str | None = None
    subscription_name: str | None = None
    credits_limit: float | None = None
    credits_remaining: float | None = None
    credits_used: float | None = None
    credits_currency: str | None = None
    credits_label: str | None = None
    credits_available: bool = False
    error: str | None = None
    owner_tag: str | None = None
    new_api_present: bool = False
    new_api_created: bool = False
    new_api_channel_id: int | None = None
    new_api_name: str | None = None
    new_api_status: int | None = None
    new_api_status_label: str | None = None
    new_api_priority: int | None = None
    new_api_weight: int | None = None
    new_api_error: str | None = None


class KimiDeployResponse(BaseModel):
    ok_count: int
    fail_count: int
    results: list[KimiDeployResult]


class KimiNewApiChannel(BaseModel):
    id: int | None = None
    name: str | None = None
    status: int | None = None
    status_label: str | None = None
    tag: str | None = None
    group: str | None = None
    priority: int | None = None
    weight: int | None = None
    base_url: str | None = None
    resource_name: str | None = None
    models: str | None = None


class KimiNewApiPool(BaseModel):
    ok: bool
    gateway: str | None = None
    next_name: str | None = None
    channels: list[KimiNewApiChannel] = Field(default_factory=list)
    error: str | None = None
    auth_expired: bool = False


class KimiNewApiAuth(BaseModel):
    ok: bool
    gateway: str = "O1"
    auth_expired: bool = False
    error: str | None = None


class KimiNewApiRequest(BaseModel):
    accounts: list[dict[str, Any]] = Field(min_length=1)
    priority: int = Field(default=13, ge=0, le=10000)
    weight: int = Field(default=1, ge=1, le=10000)


class KimiNewApiRenameRequest(BaseModel):
    name: str = Field(default="", max_length=128)
    priority: int | None = Field(default=None, ge=0, le=10000)
    weight: int | None = Field(default=None, ge=1, le=10000)
    channel_id: int | None = None
    subscription_id: str = ""
    account_name: str = ""
    azure_openai_endpoint: str = ""

    @model_validator(mode="after")
    def require_update(self):
        if not self.name.strip() and self.priority is None and self.weight is None:
            raise ValueError("Provide a channel name, priority, or weight.")
        return self


class KimiStoredAccount(BaseModel):
    name: str | None = None
    account_holder: str | None = None
    AZURE_TENANT_ID: str | None = None
    AZURE_CLIENT_ID: str | None = None
    AZURE_SUBSCRIPTION_ID: str
    subscription_name: str | None = None
    owner_tag: str | None = None


class KimiStoredResponse(BaseModel):
    accounts: list[KimiStoredAccount]


class KimiSecretsRow(BaseModel):
    ok: bool
    name: str | None = None
    account_holder: str | None = None
    AZURE_TENANT_ID: str | None = None
    AZURE_CLIENT_ID: str | None = None
    AZURE_CLIENT_SECRET: str | None = None
    AZURE_SUBSCRIPTION_ID: str | None = None
    subscription_name: str | None = None
    error: str | None = None


class KimiRegenerateResponse(BaseModel):
    ok_count: int
    fail_count: int
    results: list[KimiSecretsRow]


class KimiDeleteResult(BaseModel):
    ok: bool
    name: str | None = None
    account_name: str | None = None
    resource_group: str | None = None
    subscription_id: str | None = None
    subscription_name: str | None = None
    deleted: list[str] = Field(default_factory=list)
    message: str | None = None
    error: str | None = None


class KimiDeleteResponse(BaseModel):
    ok_count: int
    fail_count: int
    results: list[KimiDeleteResult]


class KimiTestResult(BaseModel):
    ok: bool
    name: str | None = None
    account_name: str | None = None
    deployment_name: str | None = None
    endpoint: str | None = None
    latency_ms: int | None = None
    reply: str | None = None
    error: str | None = None


class KimiTestResponse(BaseModel):
    ok_count: int
    fail_count: int
    results: list[KimiTestResult]


class KimiDeployStatus(BaseModel):
    az_cli: bool
    az_path: str | None = None
    script_found: bool
    script_path: str | None = None
    ready: bool
    message: str
    can_bootstrap: bool = False
    az_user: str | None = None
    az_user_type: str | None = None
    subscription_id: str | None = None
    subscription_name: str | None = None
    bootstrap_message: str = ""
