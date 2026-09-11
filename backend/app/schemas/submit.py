from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class SubmitSubscription(BaseModel):
    subscription_id: str
    name: str = ""
    tenant_id: str = ""
    is_default: bool = False


class SubmitSessionCreated(BaseModel):
    session_id: str
    status: str


class SubmitSessionSnapshot(BaseModel):
    session_id: str
    status: str
    account_holder: str | None = None
    person_associated: str | None = None
    group_tag: str = "sb"
    subscription_id: str | None = None
    subscription_name: str | None = None
    device_user_code: str | None = None
    device_verification_uri: str | None = None
    subscriptions: list[SubmitSubscription] = Field(default_factory=list)
    error: str | None = None
    billing_error: str | None = None
    message: str | None = None


class SubmitNamesResponse(BaseModel):
    names: list[str]


class SubmitCommitRequest(BaseModel):
    subscription_id: str = Field(min_length=1, max_length=64)
    person_associated: str = Field(min_length=1, max_length=64)
    # "sb" or "vcs"; anything else is read as SB by the service.
    group_tag: str = Field(default="sb", max_length=8)


class JoinUnlockRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128)


class PendingRequestPublic(BaseModel):
    id: int
    status: str
    person_associated: str | None = None
    group_tag: str = "sb"
    account_holder: str | None = None
    name: str | None = None
    subscription_id: str | None = None
    subscription_name: str | None = None
    tenant_id: str | None = None
    billing_error: str | None = None
    error_message: str | None = None
    error_kind: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    approved_at: datetime | None = None
    rejected_at: datetime | None = None
    can_retry_deploy: bool = False
    credits_limit: float | None = None
    credits_remaining: float | None = None
    credits_used: float | None = None
    credits_currency: str | None = None
    credits_label: str | None = None
    credits_available: bool = False
    credits_fetched_at: datetime | None = None
    credits_error: str | None = None
    grant_usd: float | None = None
    grant_tier: str | None = None


class PendingGrantSummary(BaseModel):
    total: int = 0
    fetched: int = 0
    missing: int = 0
    failed: int = 0
    pool_usd: float = 0
    count_10k: int = 0
    count_1k: int = 0
    count_other: int = 0
    pool_10k_usd: float = 0
    pool_1k_usd: float = 0
    fetched_at: datetime | None = None


class PendingGrantAccount(BaseModel):
    id: int
    email: str | None = None
    name: str | None = None
    person_associated: str | None = None
    group_tag: str = "sb"
    subscription_id: str | None = None
    credits_limit: float | None = None
    credits_remaining: float | None = None
    credits_currency: str | None = None
    credits_available: bool = False
    credits_fetched_at: datetime | None = None
    credits_error: str | None = None
    grant_usd: float | None = None
    grant_tier: str | None = None


class PendingGrantsResponse(BaseModel):
    ok: bool = True
    summary: PendingGrantSummary
    accounts: list[PendingGrantAccount] = Field(default_factory=list)


class PendingListResponse(BaseModel):
    requests: list[PendingRequestPublic]
    pending_count: int = 0
    failed_count: int = 0
    grant_summary: PendingGrantSummary = Field(default_factory=PendingGrantSummary)


class PendingDeclineResponse(BaseModel):
    ok: bool = True
    deleted_id: int
    subscription_id: str | None = None


class PendingApproveRequest(BaseModel):
    jobs: int = Field(default=1, ge=1, le=64)
    new_api_priority: int | None = Field(default=None, ge=0, le=10000)
    new_api_weight: int | None = Field(default=None, ge=1, le=10000)


class PendingApproveAccepted(BaseModel):
    ok: bool = True
    request_id: int
    status: str


class PendingBatchApproveRequest(BaseModel):
    ids: list[int] | None = None
    retry: bool = False
    # Restrict a no-ids sweep to one group. None means every group.
    group: str | None = Field(default=None, max_length=8)
    new_api_priority: int | None = Field(default=None, ge=0, le=10000)
    new_api_weight: int | None = Field(default=None, ge=1, le=10000)


class PendingBatchSkipped(BaseModel):
    id: int
    error: str


class PendingBatchApproveResponse(BaseModel):
    ok: bool = True
    started: list[int] = Field(default_factory=list)
    skipped: list[PendingBatchSkipped] = Field(default_factory=list)
