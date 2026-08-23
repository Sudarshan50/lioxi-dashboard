from datetime import date

from sqlalchemy import Date, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CostSnapshot(Base):
    """Actual billed cost per account per day, from Azure Cost Management."""

    __tablename__ = "cost_snapshots"
    __table_args__ = (UniqueConstraint("provider_account_id", "usage_date", name="uq_account_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider_account_id: Mapped[int] = mapped_column(ForeignKey("provider_accounts.id", ondelete="CASCADE"))
    usage_date: Mapped[date] = mapped_column(Date)
    actual_cost_usd: Mapped[float] = mapped_column(Float, default=0)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
