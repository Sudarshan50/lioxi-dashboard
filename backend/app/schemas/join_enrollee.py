from datetime import datetime

from pydantic import BaseModel, Field


class JoinEnrolleePublic(BaseModel):
    id: int
    name: str
    banned: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


class JoinEnrolleeCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class JoinEnrolleeUpdateRequest(BaseModel):
    banned: bool


class JoinAutoApproveRequest(BaseModel):
    enabled: bool


class JoinAutoApproveResponse(BaseModel):
    auto_approve: bool
    started: list[int] = Field(default_factory=list)
    skipped: list[int] = Field(default_factory=list)


class BanSettingsResponse(BaseModel):
    auto_approve: bool
    names: list[JoinEnrolleePublic] = Field(default_factory=list)
