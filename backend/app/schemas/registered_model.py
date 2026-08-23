from datetime import datetime

from pydantic import BaseModel, ConfigDict


class RegisteredModelCreateRequest(BaseModel):
    name: str
    input_price_per_million: float
    cached_input_price_per_million: float = 0
    output_price_per_million: float
    currency: str = "USD"


class RegisteredModelUpdateRequest(BaseModel):
    input_price_per_million: float | None = None
    cached_input_price_per_million: float | None = None
    output_price_per_million: float | None = None
    currency: str | None = None


class RegisteredModelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: int
    name: str
    input_price_per_million: float
    cached_input_price_per_million: float
    output_price_per_million: float
    currency: str
    deployments_count: int = 0
    created_at: datetime
    updated_at: datetime
