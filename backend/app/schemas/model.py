from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ModelCreateRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    provider_account_id: int
    deployment_name: str
    registered_model_id: int


class ModelUpdateRequest(BaseModel):
    enabled: bool | None = None
    registered_model_id: int | None = None


class ModelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: int
    provider_account_id: int
    provider_account_name: str
    deployment_name: str
    registered_model_id: int
    model_name: str
    input_price_per_million: float
    cached_input_price_per_million: float
    output_price_per_million: float
    currency: str
    enabled: bool
    created_at: datetime
