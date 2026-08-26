from typing import Any

from pydantic import BaseModel, Field


class KimiDeployRequest(BaseModel):
    accounts: list[dict[str, Any]] = Field(min_length=1)
    jobs: int = Field(default=32, ge=1, le=64)


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
    api_key: str | None = None
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


class KimiDeployResponse(BaseModel):
    ok_count: int
    fail_count: int
    results: list[KimiDeployResult]


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
