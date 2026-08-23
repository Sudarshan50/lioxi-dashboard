from pydantic import BaseModel


class DashboardOverview(BaseModel):
    total_tokens: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_cached_tokens: int
    total_requests: int
    estimated_cost_usd: float
    estimated_cost: float
    estimated_cost_currency: str
    actual_cost: float | None = None
    actual_cost_currency: str
    new_api_cost: float = 0
    new_api_cost_o1: float = 0
    new_api_cost_o2: float = 0
    new_api_cost_currency: str = "USD"
    accounts_count: int
    models_count: int
    avg_tpm: float
    avg_rpm: float
    peak_tpm: float
    peak_rpm: float


class TimeseriesPoint(BaseModel):
    bucket: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    requests: int = 0
    estimated_cost_usd: float


class AccountTpmPoint(BaseModel):
    bucket: str
    account_id: int
    account_name: str
    tpm: float


class BreakdownItem(BaseModel):
    id: int
    name: str
    total_tokens: int
    requests: int
    estimated_cost_usd: float
    estimated_cost: float
    currency: str = "USD"
    actual_cost: float | None = None
    actual_cost_currency: str | None = None
    new_api_cost: float | None = None
    new_api_cost_o1: float = 0
    new_api_cost_o2: float = 0
    credits_limit: float | None = None
    credits_currency: str | None = None
    avg_tpm: float = 0


class FxRate(BaseModel):
    usd_inr: float
    base: str = "USD"
    quote: str = "INR"
    source: str = "live"
    is_fallback: bool = False
