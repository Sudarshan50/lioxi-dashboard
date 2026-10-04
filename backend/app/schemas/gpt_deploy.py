from datetime import datetime

from pydantic import BaseModel, Field


class GptAccountRow(BaseModel):
    id: int
    name: str
    owner_tag: str = ""
    location: str = ""
    resource_name: str = ""
    new_api_name: str = ""
    new_api_status: int | None = None
    blocked: bool = False
    gpt_deployed: bool = False
    spend_usd: float | None = None
    grant_usd: float | None = None
    stop_at_usd: float | None = None


class GptModelPlan(BaseModel):
    name: str
    version: str | None = None
    available: bool = False
    sku: str | None = None
    capacity: int | None = None
    tpm: int | None = None
    rpm: int | None = None
    quota_limit: int | None = None
    quota_used: int | None = None
    deployed: bool = False
    deployed_capacity: int | None = None
    deployed_sku: str | None = None
    reason: str | None = None


class GptAvailability(BaseModel):
    account_id: int
    account_name: str
    location: str = ""
    endpoint: str = ""
    new_api_name: str = ""
    spend_usd: float | None = None
    grant_usd: float | None = None
    stop_at_usd: float | None = None
    eligible: bool = False
    eligibility_error: str | None = None
    models: list[GptModelPlan] = Field(default_factory=list)


class GptDeployRequest(BaseModel):
    account_id: int | None = None
    account_ids: list[int] = Field(default_factory=list)
    stack: bool = False
    models: list[str] = Field(default_factory=list)


class GptJob(BaseModel):
    running: bool = False
    job_id: str | None = None
    account_id: int | None = None
    account_name: str | None = None
    action: str | None = None
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    total: int = 0
    done: int = 0
    failed: int = 0
    current: str | None = None
    skipped: list[str] = Field(default_factory=list)


class GptLogRow(BaseModel):
    id: int
    account_id: int | None = None
    account_name: str = ""
    action: str = ""
    level: str = "info"
    message: str = ""
    created_at: datetime | None = None


class GptLogList(BaseModel):
    items: list[GptLogRow] = Field(default_factory=list)
