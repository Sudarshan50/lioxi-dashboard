from datetime import datetime, timedelta, timezone

from app.providers.base import TokenUsage

TOKEN_RANGE_MAP = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
}


def cache_end(now: datetime | None = None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.replace(minute=0, second=0, microsecond=0)


def totals_by_range(points: list[TokenUsage], end: datetime) -> dict[str, dict[str, int]]:
    aware_end = end if end.tzinfo else end.replace(tzinfo=timezone.utc)
    totals = {key: {"input": 0, "output": 0} for key in TOKEN_RANGE_MAP}
    for point in points:
        bucket = point.bucket_start
        if bucket.tzinfo is None:
            bucket = bucket.replace(tzinfo=timezone.utc)
        for key, delta in TOKEN_RANGE_MAP.items():
            start = aware_end - delta
            if start <= bucket < aware_end:
                totals[key]["input"] += int(point.prompt_tokens or 0)
                totals[key]["output"] += int(point.completion_tokens or 0)
    return totals


def cached_io_with_missing(accounts: list, range_key: str) -> tuple[int, int, list[int]]:
    key = range_key if range_key in TOKEN_RANGE_MAP else "7d"
    prompt = 0
    completion = 0
    missing: list[int] = []
    for account in accounts:
        entry = (getattr(account, "azure_token_totals", None) or {}).get(key)
        if isinstance(entry, dict):
            prompt += int(entry.get("input") or 0)
            completion += int(entry.get("output") or 0)
            continue
        account_id = getattr(account, "id", None)
        missing.append(int(account_id) if account_id is not None else 0)
    return prompt, completion, missing


async def apply_cached_account_tokens(
    overview: dict,
    accounts: list,
    range_key: str,
    model_id: int | None,
    snapshot_totals,
) -> None:
    if model_id is not None or not accounts:
        return
    prompt, completion, missing = cached_io_with_missing(accounts, range_key)
    if len(missing) == len(accounts):
        return
    real_missing = [account_id for account_id in missing if account_id > 0]
    if real_missing:
        extra = await snapshot_totals(real_missing)
        prompt += int(extra.get("total_prompt_tokens") or 0)
        completion += int(extra.get("total_completion_tokens") or 0)
    overview["total_prompt_tokens"] = prompt
    overview["total_completion_tokens"] = completion
