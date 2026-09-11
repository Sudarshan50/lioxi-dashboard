from datetime import datetime

from pydantic import BaseModel, Field


class JoinEnrolleePublic(BaseModel):
    id: int
    name: str
    group_tag: str = "sb"
    banned: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


class JoinEnrolleeCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    group: str = Field(default="sb", max_length=8)


class JoinEnrolleeUpdateRequest(BaseModel):
    banned: bool


class JoinAutoApproveRequest(BaseModel):
    enabled: bool
    group: str = Field(default="sb", max_length=8)


class JoinAutoApproveResponse(BaseModel):
    group: str = "sb"
    auto_approve: bool
    started: list[int] = Field(default_factory=list)
    skipped: list[int] = Field(default_factory=list)


class BanSettingsResponse(BaseModel):
    # SB toggle, kept under the original field name for existing clients.
    auto_approve: bool
    auto_approve_vcs: bool = False
    names: list[JoinEnrolleePublic] = Field(default_factory=list)
