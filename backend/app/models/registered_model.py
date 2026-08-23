from sqlalchemy import Float, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import TimestampMixin


class RegisteredModel(Base, TimestampMixin):
    """A model registered once by name with its pricing. Deployments across any
    number of accounts link to one of these instead of each carrying their own
    copy of the name/price, so pricing a model is a one-time action - exactly
    like registering a model before using it in a multi-channel LLM gateway.
    """

    __tablename__ = "registered_models"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)

    input_price_per_million: Mapped[float] = mapped_column(Float)
    cached_input_price_per_million: Mapped[float] = mapped_column(Float, default=0)
    output_price_per_million: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
