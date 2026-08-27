"""Redis cache for Deploy K3 Azure inventory (Foundry stack + credits).

NewAPI channel fields are never stored. A cache miss or Redis outage falls
through to live Azure ARM calls.
"""

from __future__ import annotations

import json
import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.config import get_settings
from app.schemas.kimi_deploy import KimiCreditSnapshot, KimiDeployResult

logger = logging.getLogger(__name__)

_client: Redis | None = None
_warned = False


def _pointer_key(subscription_id: str) -> str:
    return f"kimi:azure:inventory:{subscription_id.strip().lower()}"


def _blob_key(subscription_id: str, resource_name: str) -> str:
    return f"kimi:azure:inventory:{subscription_id.strip().lower()}:{resource_name.strip().lower()}"


def _looks_like_json(raw: str) -> bool:
    text = (raw or "").lstrip()
    return text.startswith("{") or text.startswith("[")


def _clear_newapi(payload: dict) -> dict:
    payload["new_api_present"] = False
    payload["new_api_created"] = False
    payload["new_api_channel_id"] = None
    payload["new_api_name"] = None
    payload["new_api_status"] = None
    payload["new_api_status_label"] = None
    payload["new_api_priority"] = None
    payload["new_api_weight"] = None
    payload["new_api_error"] = None
    payload["owner_tag"] = None
    return payload


def _azure_payload(result: KimiDeployResult) -> dict:
    payload = result.model_dump(mode="json")
    return _clear_newapi(payload)


def _client_or_none() -> Redis | None:
    global _client, _warned
    url = (get_settings().redis_url or "").strip()
    if not url:
        return None
    if _client is None:
        _client = Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=0.4,
            socket_timeout=0.8,
        )
    return _client


def _warn(exc: BaseException) -> None:
    global _warned
    if _warned:
        return
    _warned = True
    logger.warning("Redis Azure inventory cache unavailable: %s", exc)


async def _delete_keys(client: Redis, *keys: str) -> None:
    names = [key for key in keys if key]
    if not names:
        return
    try:
        await client.delete(*names)
    except RedisError as exc:
        logger.warning("Redis Azure inventory cache delete failed: %s", exc)


async def close_azure_inventory_cache() -> None:
    global _client, _warned
    client = _client
    _client = None
    _warned = False
    if client is None:
        return
    try:
        await client.aclose()
    except Exception:  # noqa: BLE001
        logger.debug("Redis close failed", exc_info=True)


async def get_cached_azure_inventory(
    subscription_id: str | None,
    resource_name: str | None = None,
) -> KimiDeployResult | None:
    sub = (subscription_id or "").strip()
    if not sub:
        return None
    client = _client_or_none()
    if client is None:
        return None
    resource = (resource_name or "").strip()
    key = ""
    try:
        if resource:
            key = _blob_key(sub, resource)
        else:
            pointer = await client.get(_pointer_key(sub))
            if not pointer:
                return None
            if _looks_like_json(pointer):
                await _delete_keys(client, _pointer_key(sub))
                return None
            resource = str(pointer).strip()
            if not resource:
                return None
            key = _blob_key(sub, resource)
        raw = await client.get(key)
    except RedisError as exc:
        _warn(exc)
        return None
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        await _delete_keys(client, key)
        return None
    if not isinstance(payload, dict):
        await _delete_keys(client, key)
        return None
    try:
        return KimiDeployResult.model_validate(_clear_newapi(payload))
    except Exception:  # noqa: BLE001
        logger.debug("Ignoring invalid Azure inventory cache for %s", sub[-12:], exc_info=True)
        await _delete_keys(client, key)
        return None


async def store_azure_inventory(result: KimiDeployResult) -> None:
    sub = (result.subscription_id or "").strip()
    resource = (result.account_name or "").strip()
    if not sub or not resource or not result.ok or result.error or not result.credits_available:
        return
    client = _client_or_none()
    if client is None:
        return
    ttl = max(30, int(get_settings().kimi_azure_cache_ttl_seconds or 900))
    try:
        await client.set(_blob_key(sub, resource), json.dumps(_azure_payload(result)), ex=ttl)
        await client.set(_pointer_key(sub), resource, ex=ttl)
    except RedisError as exc:
        _warn(exc)


async def drop_azure_inventory(subscription_id: str | None, resource_name: str | None = None) -> None:
    sub = (subscription_id or "").strip()
    if not sub:
        return
    client = _client_or_none()
    if client is None:
        return
    pointer = _pointer_key(sub)
    resource = (resource_name or "").strip()
    keys = [pointer]
    if resource:
        keys.append(_blob_key(sub, resource))
    else:
        try:
            pointed = await client.get(pointer)
        except RedisError as exc:
            logger.warning("Redis Azure inventory cache delete failed: %s", exc)
            pointed = None
        if pointed and not _looks_like_json(str(pointed)):
            name = str(pointed).strip()
            if name:
                keys.append(_blob_key(sub, name))
    await _delete_keys(client, *keys)


def credits_from_inventory(result: KimiDeployResult, account: dict[str, str]) -> KimiCreditSnapshot:
    name = account.get("name") or account.get("account_holder") or result.name or "account"
    return KimiCreditSnapshot(
        ok=bool(result.credits_available),
        name=name,
        subscription_id=result.subscription_id or account.get("AZURE_SUBSCRIPTION_ID") or "",
        subscription_name=result.subscription_name or account.get("subscription_name") or None,
        credits_limit=result.credits_limit,
        credits_remaining=result.credits_remaining,
        credits_used=result.credits_used,
        credits_currency=result.credits_currency,
        credits_label=result.credits_label,
        credits_available=bool(result.credits_available),
        error=None if result.credits_available else "Azure did not return a credit grant for this subscription.",
    )
