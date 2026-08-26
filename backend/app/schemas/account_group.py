from datetime import datetime

from pydantic import BaseModel


class AccountGroupCreateRequest(BaseModel):
    name: str
    account_ids: list[int] = []


class AccountGroupUpdateRequest(BaseModel):
    name: str | None = None
    account_ids: list[int] | None = None


class AccountSummary(BaseModel):
    id: int
    name: str


class AccountGroupResponse(BaseModel):
    id: int
    name: str
    accounts: list[AccountSummary]
    created_at: datetime
    auto: bool = False
