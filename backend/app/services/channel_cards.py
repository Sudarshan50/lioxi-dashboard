"""One card per NewAPI channel on an Azure resource.

Spend and on/off belong to the channel. The account stop stays on the
combined total in new_api_service.
"""

import logging

from app.config import get_settings
from app.services.new_api_service import (
    _channel_id,
    _channel_status,
    _host_key,
    _unique_channels,
    fetch_channels,
    gateways,
)

logger = logging.getLogger(__name__)


def _is_gpt_channel(channel: dict) -> bool:
    tag = str(channel.get("tag") or "").strip().lower()
    name = str(channel.get("name") or "").lower()
    return tag == "gpt-astra" or name.startswith("gpt-astra-proxy")


def cards_for_channels(channels: list[dict], gateway_label: str, unit: float) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    scale = unit if unit > 0 else 1
    for channel in _unique_channels(channels):
        host = _host_key(channel.get("base_url"))
        if not host:
            continue
        grouped.setdefault(host, []).append(
            {
                "id": _channel_id(channel),
                "name": str(channel.get("name") or "").strip(),
                "role": "gpt" if _is_gpt_channel(channel) else "primary",
                "gateway": gateway_label,
                "status": _channel_status(channel.get("status")),
                "spend_usd": float(channel.get("used_quota") or 0) / scale,
            }
        )
    for cards in grouped.values():
        cards.sort(key=lambda card: (card["role"] == "gpt", card["name"]))
    return grouped


def add_gpt_pool(merged: dict[str, list[dict]], pool: dict[str, list[dict]]) -> None:
    """Add the GPT portal's spend onto the card with the same channel name."""
    for host, extras in pool.items():
        by_name: dict[str, float] = {}
        for card in extras:
            name = card["name"]
            if not name:
                continue
            by_name[name] = by_name.get(name, 0.0) + float(card["spend_usd"] or 0)
        for card in merged.get(host, []):
            extra = by_name.pop(card["name"], None)
            if extra:
                card["spend_usd"] = float(card["spend_usd"] or 0) + extra


async def cards_by_host() -> dict[str, list[dict]]:
    unit = get_settings().new_api_quota_per_unit or 1
    merged: dict[str, list[dict]] = {}
    pool: dict[str, list[dict]] = {}
    for gateway in gateways():
        try:
            channels = await fetch_channels(gateway)
        except Exception:
            logger.warning("Channel cards skipped for %s", gateway.label, exc_info=True)
            continue
        grouped = cards_for_channels(channels, gateway.label, unit)
        if gateway.label == "GPT":
            for host, cards in grouped.items():
                pool.setdefault(host, []).extend(cards)
            continue
        for host, cards in grouped.items():
            merged.setdefault(host, []).extend(cards)
    add_gpt_pool(merged, pool)
    return merged
