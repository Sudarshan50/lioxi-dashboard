from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import TimestampMixin


class AccountGroup(Base, TimestampMixin):
    """A named collection of provider accounts (e.g. a team or environment)
    that usage can be viewed and filtered by, in addition to individual
    accounts.
    """

    __tablename__ = "account_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
