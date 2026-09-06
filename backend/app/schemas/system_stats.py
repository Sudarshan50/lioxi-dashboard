from datetime import datetime

from pydantic import BaseModel


class ResourceMetric(BaseModel):
    used_bytes: int
    total_bytes: int
    available_bytes: int
    percent: float


class SystemStats(BaseModel):
    cpu_percent: float | None
    cpu_count: int
    load_avg_1: float
    load_avg_5: float
    load_avg_15: float
    memory: ResourceMetric
    storage: ResourceMetric
    collected_at: datetime
