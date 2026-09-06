from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import TimestampMixin


class JoinEnrollee(Base, TimestampMixin):
    __tablename__ = "join_enrollees"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    banned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
