from datetime import datetime

from pydantic import BaseModel


class NotificationItem(BaseModel):
    id: int
    kind: str
    status: str
    email: str | None = None
    owner_tag: str | None = None
    account_name: str | None = None
    resource_name: str | None = None
    subscription_id: str | None = None
    new_api_name: str | None = None
    detail: str
    error: str | None = None
    created_at: datetime


class NotificationListResponse(BaseModel):
    items: list[NotificationItem]
    total: int
