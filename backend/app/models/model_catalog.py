from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin
from app.models.registered_model import RegisteredModel


class MonitoredModel(Base, TimestampMixin):
    """Links one account's live deployment to a registered model. Carries no
    pricing or naming of its own - that lives on RegisteredModel so the same
    model deployed under many accounts is priced and named exactly once.
    """

    __tablename__ = "monitored_models"
    __table_args__ = (UniqueConstraint("provider_account_id", "deployment_name", name="uq_account_deployment"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider_account_id: Mapped[int] = mapped_column(ForeignKey("provider_accounts.id", ondelete="CASCADE"))
    registered_model_id: Mapped[int] = mapped_column(ForeignKey("registered_models.id", ondelete="RESTRICT"))

    deployment_name: Mapped[str] = mapped_column(String(128))

    enabled: Mapped[bool] = mapped_column(default=True)

    registered_model: Mapped[RegisteredModel] = relationship(lazy="joined")
