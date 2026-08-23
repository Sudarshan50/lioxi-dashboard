from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class UsageSnapshot(Base):
    """One row per (model, hourly bucket), upserted on every sync run."""

    __tablename__ = "usage_snapshots"
    __table_args__ = (UniqueConstraint("monitored_model_id", "bucket_start", name="uq_model_bucket"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider_account_id: Mapped[int] = mapped_column(ForeignKey("provider_accounts.id", ondelete="CASCADE"))
    monitored_model_id: Mapped[int] = mapped_column(ForeignKey("monitored_models.id", ondelete="CASCADE"))

    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0)
