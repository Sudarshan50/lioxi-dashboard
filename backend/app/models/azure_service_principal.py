from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import TimestampMixin


class AzureServicePrincipal(Base, TimestampMixin):
    """Azure service principal used for Deploy K3 and later automation. Secret is stored encrypted."""

    __tablename__ = "azure_service_principals"

    id: Mapped[int] = mapped_column(primary_key=True)
    subscription_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    client_id: Mapped[str] = mapped_column(String(64))
    client_secret_encrypted: Mapped[str] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(String(128), nullable=True, default=None)
    account_holder: Mapped[str | None] = mapped_column(String(256), nullable=True, default=None)
    subscription_name: Mapped[str | None] = mapped_column(String(256), nullable=True, default=None)
    owner_tag: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
