from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import TimestampMixin


class SpSubmitRequest(Base, TimestampMixin):
    """User-submitted Azure SP waiting for admin K3 deploy. Secret is stored encrypted."""

    __tablename__ = "sp_submit_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), index=True, default="login_started")
    person_associated: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    account_holder: Mapped[str | None] = mapped_column(String(256), nullable=True, default=None)
    subscription_id: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None, index=True)
    subscription_name: Mapped[str | None] = mapped_column(String(256), nullable=True, default=None)
    tenant_id: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    client_id: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    client_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    name: Mapped[str | None] = mapped_column(String(128), nullable=True, default=None)
    sp_display_name: Mapped[str | None] = mapped_column(String(128), nullable=True, default=None)
    billing_error: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    error_kind: Mapped[str | None] = mapped_column(String(16), nullable=True, default=None)
    device_user_code: Mapped[str | None] = mapped_column(String(32), nullable=True, default=None)
    device_verification_uri: Mapped[str | None] = mapped_column(String(256), nullable=True, default=None)
    subscriptions_json: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    az_config_dir: Mapped[str | None] = mapped_column(String(512), nullable=True, default=None)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
