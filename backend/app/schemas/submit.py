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


class PendingRequestPublic(BaseModel):
    id: int
    status: str
    person_associated: str | None = None
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


class PendingListResponse(BaseModel):
    requests: list[PendingRequestPublic]
    pending_count: int = 0
    failed_count: int = 0


class PendingDeclineResponse(BaseModel):
    ok: bool = True
    deleted_id: int
    subscription_id: str | None = None


class PendingApproveRequest(BaseModel):
    jobs: int = Field(default=1, ge=1, le=64)
    new_api_priority: int = Field(default=13, ge=0, le=10000)
    new_api_weight: int = Field(default=1, ge=1, le=10000)
