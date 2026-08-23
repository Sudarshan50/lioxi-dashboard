from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import TimestampMixin


class ProviderAccount(Base, TimestampMixin):
    __tablename__ = "provider_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    provider_type: Mapped[str] = mapped_column(String(32), default="azure_openai")

    tenant_id: Mapped[str] = mapped_column(String(64))
    client_id: Mapped[str] = mapped_column(String(64))
    client_secret_encrypted: Mapped[str] = mapped_column(Text)
    subscription_id: Mapped[str] = mapped_column(String(64))

    resource_id: Mapped[str] = mapped_column(Text)
    resource_group: Mapped[str] = mapped_column(String(128))
    resource_name: Mapped[str] = mapped_column(String(128))
    endpoint: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(32))
    location: Mapped[str] = mapped_column(String(64))

    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    last_sync_status: Mapped[str | None] = mapped_column(String(16), nullable=True, default=None)
    last_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

    credits_remaining: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    credits_used: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    credits_limit: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    credits_currency: Mapped[str | None] = mapped_column(String(8), nullable=True, default=None)
    credits_unit: Mapped[str | None] = mapped_column(String(16), nullable=True, default=None)
    credits_label: Mapped[str | None] = mapped_column(String(256), nullable=True, default=None)
    credits_available: Mapped[bool] = mapped_column(default=False)

    new_api_gateway: Mapped[str | None] = mapped_column(String(8), nullable=True, default=None)
    new_api_channel_id: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    new_api_name: Mapped[str | None] = mapped_column(String(128), nullable=True, default=None)
    new_api_tag: Mapped[str | None] = mapped_column(String(128), nullable=True, default=None)
    new_api_used_quota: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    new_api_cost_o1_usd: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    new_api_cost_o2_usd: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    new_api_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    new_api_status: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    new_api_status_o1: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    new_api_status_o2: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    new_api_weight: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    new_api_priority: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    new_api_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    # Highest Azure-consumed % already announced (75/95). 100 = exhausted auto-stop announced.
    new_api_alert_level: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
