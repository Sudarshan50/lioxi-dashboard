from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AccountGroupMember(Base):
    """Many-to-many link between an account group and a provider account -
    an account may belong to more than one group (e.g. by team and by
    environment)."""

    __tablename__ = "account_group_members"
    __table_args__ = (UniqueConstraint("account_group_id", "provider_account_id", name="uq_group_account"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_group_id: Mapped[int] = mapped_column(ForeignKey("account_groups.id", ondelete="CASCADE"))
    provider_account_id: Mapped[int] = mapped_column(ForeignKey("provider_accounts.id", ondelete="CASCADE"))
