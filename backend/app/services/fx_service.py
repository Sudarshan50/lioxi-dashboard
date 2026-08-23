import logging
import time

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

_FALLBACK_USD_INR = 87.0
_CACHE_TTL_SECONDS = 3600.0
_cache: tuple[float, float] | None = None


async def usd_to_inr_quote() -> dict:
    configured = get_settings().usd_inr_rate
    if configured and configured > 0:
        return {"usd_inr": configured, "source": "config", "is_fallback": False}
    global _cache
    now = time.monotonic()
    if _cache and _cache[1] > now:
        return {"usd_inr": _cache[0], "source": "live", "is_fallback": False}
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get("https://open.er-api.com/v6/latest/USD")
            response.raise_for_status()
            rate = float((response.json().get("rates") or {}).get("INR") or 0)
        if rate > 0:
            _cache = (rate, now + _CACHE_TTL_SECONDS)
            return {"usd_inr": rate, "source": "live", "is_fallback": False}
    except Exception:
        logger.warning("Could not fetch USD/INR rate", exc_info=True)
    if _cache:
        return {"usd_inr": _cache[0], "source": "live", "is_fallback": False}
    return {"usd_inr": _FALLBACK_USD_INR, "source": "fallback", "is_fallback": True}


async def usd_to_inr_rate() -> float:
    return float((await usd_to_inr_quote())["usd_inr"])
