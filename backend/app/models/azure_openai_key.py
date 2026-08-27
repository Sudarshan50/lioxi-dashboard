from sqlalchemy import String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import TimestampMixin


class AzureOpenaiKey(Base, TimestampMixin):
    """Data-plane OpenAI key for a Foundry resource, stored encrypted for NewAPI automation."""

    __tablename__ = "azure_openai_keys"
    __table_args__ = (
        UniqueConstraint("subscription_id", "resource_name", name="uq_azure_openai_keys_sub_resource"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    subscription_id: Mapped[str] = mapped_column(String(64), index=True)
    resource_name: Mapped[str] = mapped_column(String(128))
    resource_group: Mapped[str | None] = mapped_column(String(128), nullable=True, default=None)
    endpoint: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    deployment_name: Mapped[str | None] = mapped_column(String(128), nullable=True, default=None)
    api_key_encrypted: Mapped[str] = mapped_column(Text)
    owner_tag: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
