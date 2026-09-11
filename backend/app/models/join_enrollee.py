from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import TimestampMixin


class JoinEnrollee(Base, TimestampMixin):
    __tablename__ = "join_enrollees"
    # Uniqueness is (lower(name), group_tag) -- the same person may be enrolled
    # under both groups, but not twice within one. It is declared only as the
    # expression index in database.py, because a UniqueConstraint here would
    # claim that index name and leave fresh databases case-sensitive.

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    group_tag: Mapped[str] = mapped_column(String(8), nullable=False, default="sb", server_default="sb", index=True)
    banned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
