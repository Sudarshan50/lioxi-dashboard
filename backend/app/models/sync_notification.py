from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import TimestampMixin


class SyncNotification(Base, TimestampMixin):
    __tablename__ = "sync_notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    email: Mapped[str | None] = mapped_column(String(256), nullable=True, default=None)
    owner_tag: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    account_name: Mapped[str | None] = mapped_column(String(128), nullable=True, default=None)
    resource_name: Mapped[str | None] = mapped_column(String(128), nullable=True, default=None)
    subscription_id: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    new_api_name: Mapped[str | None] = mapped_column(String(128), nullable=True, default=None)
    detail: Mapped[str] = mapped_column(String(256), default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
