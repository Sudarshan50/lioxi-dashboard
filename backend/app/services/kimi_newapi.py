"""Create and inspect O1 NewAPI channels for Deploy K3.

Kimi channels are created only on O1 (NEW_API_BASE_URL). O2 is never
listed, matched, or written. Channel fields clone the live O1
kimi-k3-500k-proxy-* rows (group default,azure-zr-highTPM, tag kimi-k3-pool).
Names are kimi-k3-500k-proxy-X where X is one past the highest number
already on O1 (gaps are not reused).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.account_repository import AccountRepository
from app.schemas.kimi_deploy import KimiDeployResult, KimiNewApiAuth, KimiNewApiPool
from app.services.new_api_service import (
    Gateway,
    NewApiAuthError,
    NewApiError,
    _channel_status,
    _gateway_lock,
    _headers,
    _host_key,
    _membership,
    fetch_channels,
    gateways,
    is_newapi_auth_failure,
    recompute_overall_status,
)
from app.services.openai_key_store import decrypt_foundry_key
from app.services.owner_tag import resource_key

logger = logging.getLogger(__name__)

KIMI_CHANNEL_PREFIX = "kimi-k3-500k-proxy-"
KIMI_CHANNEL_RE = re.compile(r"^kimi-k3-500k-proxy-(\d+)$", re.I)
KIMI_POOL_TAG = " kimi-k3-pool "
KIMI_CHANNEL_GROUP = "default,azure-zr-highTPM"
KIMI_CHANNEL_MODELS = "FW-Kimi-K3"
KIMI_MODEL_MAPPING = ""
KIMI_AZURE_API_VERSION = "2025-04-01-preview"
KIMI_CHANNEL_TYPE = 3  # Azure
KIMI_NEWAPI_GATEWAY = "O1"
DEFAULT_PRIORITY = 13
DEFAULT_WEIGHT = 1
KIMI_SETTING = json.dumps(
    {
        "force_format": False,
        "thinking_to_content": False,
        "proxy": "",
        "pass_through_body_enabled": False,
        "system_prompt": "",
        "system_prompt_override": False,
    },
    separators=(",", ":"),
)
KIMI_SETTINGS = json.dumps({"disable_task_polling_sleep": False}, separators=(",", ":"))
KIMI_PARAM_OVERRIDE = json.dumps(
    {
        "operations": [
            {
                "path": "messages",
                "mode": "return_error",
                "conditions": [{"path": "messages.#.content.#.type", "mode": "contains", "value": "video"}],
                "value": {"message": "当前模型不支持视频输入，请改用图片", "status_code": 400},
            },
            {"path": "audio", "mode": "delete"},
            {
                "path": "reasoning_effort",
                "mode": "set",
                "value": "high",
                "keep_origin": True,
                "conditions": [{"path": "enable_thinking", "mode": "full", "value": True}],
            },
            {
                "path": "chat_template_kwargs.enable_thinking",
                "mode": "set",
                "value": False,
                "keep_origin": True,
                "conditions": [{"path": "enable_thinking", "mode": "full", "value": False}],
            },
            {"path": "enable_thinking", "mode": "delete"},
            {"path": "thinking_budget", "mode": "delete"},
            {"path": "messages.*.content.*.cache_control", "mode": "delete"},
            {"path": "tools.*.cache_control", "mode": "delete"},
        ]
    },
    indent=2,
)

_pool_lock = asyncio.Lock()
_pool_cache: tuple[float, list[dict]] | None = None
_POOL_TTL_S = 8.0


def kimi_pool_gateway() -> Gateway:
    for gateway in gateways():
        if gateway.label == KIMI_NEWAPI_GATEWAY:
            return gateway
    raise NewApiError("O1 NewAPI is not configured (NEW_API_SYSTEM_TOKEN).")


def next_kimi_index(channels: list[dict]) -> int:
    highest = -1
    for channel in channels:
        match = KIMI_CHANNEL_RE.match(str(channel.get("name") or ""))
        if match:
            highest = max(highest, int(match.group(1)))
    return max(highest, 0) + 1


def next_kimi_channel_name(channels: list[dict]) -> str:
    return f"{KIMI_CHANNEL_PREFIX}{next_kimi_index(channels)}"


def pool_tag(channels: list[dict]) -> str:
    """Exact tag string used by existing kimi-k3-500k-proxy rows, including spaces.

    NewAPI tag mode is exact, so 'kimi-k3-pool' and ' kimi-k3-pool ' are different tags.
    """
    counts: dict[str, int] = {}
    for channel in channels:
        if not KIMI_CHANNEL_RE.match(str(channel.get("name") or "")):
            continue
        tag = channel.get("tag")
        if not isinstance(tag, str) or not tag.strip():
            continue
        counts[tag] = counts.get(tag, 0) + 1
    if not counts:
        return KIMI_POOL_TAG
    return max(counts.items(), key=lambda item: item[1])[0]


def clean_channel_name(value: str | None) -> str:
    name = " ".join((value or "").split())
    if not name:
        raise NewApiError("NewAPI channel name is required.")
    if len(name) > 128:
        raise NewApiError("NewAPI channel name must be 128 characters or fewer.")
    return name


def _name_owner(channels: list[dict], name: str, *, ignore_id: int | None = None) -> dict | None:
    wanted = name.strip().lower()
    for channel in channels:
        if (channel.get("name") or "").strip().lower() != wanted:
            continue
        if ignore_id is not None and _as_int(channel.get("id")) == ignore_id:
            continue
        return channel
    return None


def status_label(status: int | None) -> str | None:
    if status == 1:
        return "enabled"
    if status == 2:
        return "disabled"
    if status == 3:
        return "auto-disabled"
    if status is None:
        return None
    return f"status {status}"


def openai_base_url(endpoint: str | None, resource_name: str | None) -> str:
    cleaned = (endpoint or "").strip().rstrip("/")
    if cleaned:
        cleaned = cleaned.replace(".cognitiveservices.azure.com", ".openai.azure.com")
        cleaned = cleaned.replace(".services.ai.azure.com", ".openai.azure.com")
        return cleaned
    name = (resource_name or "").strip()
    if not name:
        raise NewApiError("Missing Foundry resource name for NewAPI channel.")
    return f"https://{name}.openai.azure.com"


def _as_int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clamp_int(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))


def _routing_for_account(account: dict[str, str], *, default_priority: int, default_weight: int) -> tuple[int, int]:
    priority = _as_int(account.get("new_api_priority"))
    weight = _as_int(account.get("new_api_weight"))
    if priority is None:
        priority = default_priority
    if weight is None:
        weight = default_weight
    return _clamp_int(priority, 0, 10000), _clamp_int(weight, 1, 10000)


def public_channel(channel: dict) -> dict[str, Any]:
    host = _host_key(channel.get("base_url"))
    status = _channel_status(channel.get("status"))
    return {
        "id": _as_int(channel.get("id")),
        "name": (channel.get("name") or "").strip() or None,
        "status": status,
        "status_label": status_label(status),
        "tag": channel.get("tag") or None,
        "group": (channel.get("group") or "").strip() or None,
        "priority": _as_int(channel.get("priority")),
        "weight": _as_int(channel.get("weight")),
        "base_url": channel.get("base_url"),
        "resource_name": host,
        "models": (channel.get("models") or "").strip() or None,
    }


def _is_pool_tag(value: str | None) -> bool:
    return (value or "").strip().lower() == KIMI_POOL_TAG.strip().lower()


def match_channel(channels: list[dict], hosts: set[str]) -> dict | None:
    wanted = {host.strip().lower() for host in hosts if host and host.strip()}
    if not wanted:
        return None
    named: list[tuple[dict, int]] = []
    for channel in channels:
        if _host_key(channel.get("base_url")) not in wanted:
            continue
        match = KIMI_CHANNEL_RE.match(str(channel.get("name") or ""))
        if not match:
            continue
        named.append((channel, int(match.group(1))))
    if not named:
        return None
    named.sort(key=lambda item: (0 if _channel_status(item[0].get("status")) == 1 else 1, -item[1]))
    return named[0][0]


async def list_kimi_pool_channels(*, force: bool = False) -> list[dict]:
    global _pool_cache
    now = time.monotonic()
    if not force and _pool_cache and now - _pool_cache[0] < _POOL_TTL_S:
        return _pool_cache[1]
    async with _pool_lock:
        if not force and _pool_cache and time.monotonic() - _pool_cache[0] < _POOL_TTL_S:
            return _pool_cache[1]
        channels = await fetch_channels(kimi_pool_gateway())
        _pool_cache = (time.monotonic(), channels)
        return channels


def invalidate_kimi_pool_cache() -> None:
    global _pool_cache
    _pool_cache = None


async def kimi_newapi_pool() -> KimiNewApiPool:
    try:
        channels = await list_kimi_pool_channels()
    except NewApiAuthError as exc:
        return KimiNewApiPool(ok=False, gateway="O1", auth_expired=True, error=str(exc)[:300])
    except NewApiError as exc:
        message = str(exc)[:300]
        return KimiNewApiPool(
            ok=False,
            gateway="O1",
            auth_expired=is_newapi_auth_failure(None, text=message),
            error=message,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Kimi NewAPI pool fetch failed", exc_info=True)
        return KimiNewApiPool(ok=False, error=str(exc)[:300])
    visible = [
        channel
        for channel in channels
        if KIMI_CHANNEL_RE.match(str(channel.get("name") or ""))
        or _is_pool_tag(channel.get("tag"))
    ]
    ordered = sorted(
        visible,
        key=lambda channel: (
            0 if KIMI_CHANNEL_RE.match(str(channel.get("name") or "")) else 1,
            str(channel.get("name") or ""),
        ),
    )
    return KimiNewApiPool(
        ok=True,
        gateway="O1",
        next_name=next_kimi_channel_name(channels),
        channels=[public_channel(channel) for channel in ordered],
    )


async def _hosts_for(_session: AsyncSession | None, result: KimiDeployResult, account: dict[str, str] | None) -> set[str]:
    hosts: set[str] = set()
    for value in (
        result.account_name,
        result.azure_openai_endpoint,
        (account or {}).get("account_name"),
        (account or {}).get("azure_openai_endpoint"),
    ):
        key = resource_key(value)
        if key:
            hosts.add(key)
    return hosts


def _apply_channel(result: KimiDeployResult, channel: dict, *, created: bool = False) -> None:
    public = public_channel(channel)
    result.new_api_present = True
    result.new_api_created = created
    result.new_api_channel_id = public["id"]
    result.new_api_name = public["name"]
    result.new_api_status = public["status"]
    result.new_api_status_label = public["status_label"]
    result.new_api_priority = public["priority"]
    result.new_api_weight = public["weight"]
    result.new_api_error = None


async def attach_kimi_newapi_status(
    session: AsyncSession | None,
    results: list[KimiDeployResult],
    accounts: list[dict[str, str]] | None = None,
) -> None:
    try:
        channels = await list_kimi_pool_channels()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not load Kimi NewAPI pool for status", exc_info=True)
        message = str(exc)[:300]
        for result in results:
            if not result.new_api_present:
                result.new_api_error = message
        return
    by_index = accounts or []
    for index, result in enumerate(results):
        account = by_index[index] if index < len(by_index) else None
        hosts = await _hosts_for(session, result, account)
        channel = match_channel(channels, hosts)
        if channel is not None:
            _apply_channel(result, channel)


def _channel_create_body(
    *,
    name: str,
    api_key: str,
    base_url: str,
    priority: int,
    weight: int,
) -> dict[str, Any]:
    return {
        "mode": "single",
        "channel": {
            "type": KIMI_CHANNEL_TYPE,
            "name": name,
            "key": api_key,
            "base_url": base_url,
            "other": KIMI_AZURE_API_VERSION,
            "models": KIMI_CHANNEL_MODELS,
            "group": KIMI_CHANNEL_GROUP,
            "tag": KIMI_POOL_TAG,
            "model_mapping": KIMI_MODEL_MAPPING,
            "priority": priority,
            "weight": weight,
            "auto_ban": 1,
            "status": 1,
            "setting": KIMI_SETTING,
            "settings": KIMI_SETTINGS,
            "param_override": KIMI_PARAM_OVERRIDE,
            "openai_organization": "",
            "test_model": "",
            "status_code_mapping": "",
            "header_override": "",
            "remark": "",
        },
    }


def _raise_for_channel_write(response, payload: dict | None, action: str) -> None:
    body = payload or {}
    if is_newapi_auth_failure(response.status_code, body, response.text[:200]):
        raise NewApiAuthError("O1", response.status_code, str(body.get("message") or response.text)[:200])
    if response.status_code != 200 or body.get("success") is False:
        raise NewApiError(f"O1 channel {action} failed: {(body.get('message') or response.text)[:240]}")


async def kimi_newapi_auth() -> KimiNewApiAuth:
    try:
        gateway = kimi_pool_gateway()
    except NewApiError as exc:
        return KimiNewApiAuth(ok=False, gateway="O1", auth_expired=False, error=str(exc)[:300])
    try:
        async with httpx.AsyncClient(timeout=20, proxy=gateway.proxy) as client:
            response = await client.get(
                f"{gateway.base_url}/api/channel/",
                params={"tag_mode": "false", "id_sort": "false", "p": 1, "page_size": 1},
                headers=_headers(gateway),
            )
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if is_newapi_auth_failure(response.status_code, payload if isinstance(payload, dict) else {}, response.text[:200]):
            return KimiNewApiAuth(ok=False, gateway="O1", auth_expired=True, error="O1 portal token expired")
        if response.status_code != 200 or (isinstance(payload, dict) and payload.get("success") is False):
            message = str((payload or {}).get("message") or response.text)[:240]
            return KimiNewApiAuth(
                ok=False,
                gateway="O1",
                auth_expired=is_newapi_auth_failure(response.status_code, payload if isinstance(payload, dict) else {}, message),
                error=message,
            )
        return KimiNewApiAuth(ok=True, gateway="O1", auth_expired=False)
    except NewApiAuthError:
        return KimiNewApiAuth(ok=False, gateway="O1", auth_expired=True, error="O1 portal token expired")
    except Exception as exc:  # noqa: BLE001
        return KimiNewApiAuth(ok=False, gateway="O1", auth_expired=False, error=str(exc)[:300])


async def _post_channel(gateway: Gateway, body: dict[str, Any]) -> None:
    if gateway.label != KIMI_NEWAPI_GATEWAY:
        raise NewApiError("Kimi channels are only created on O1.")
    async with httpx.AsyncClient(timeout=30, proxy=gateway.proxy) as client:
        response = await client.post(
            f"{gateway.base_url}/api/channel/",
            headers=_headers(gateway),
            json=body,
        )
    try:
        payload = response.json()
    except ValueError as exc:
        if is_newapi_auth_failure(response.status_code, text=response.text[:200]):
            raise NewApiAuthError("O1", response.status_code, response.text[:200]) from exc
        raise NewApiError(f"O1 channel create returned non-JSON ({response.status_code})") from exc
    _raise_for_channel_write(response, payload if isinstance(payload, dict) else {}, "create")


async def _stamp_portal_account(session: AsyncSession, subscription_id: str, resource_name: str, channel: dict) -> None:
    account = await AccountRepository(session).get_by_subscription_and_resource(subscription_id, resource_name)
    if account is None:
        return
    membership = _membership(account)
    membership.add("O1")
    account.new_api_gateway = "+".join(sorted(membership)) or None
    account.new_api_channel_id = channel.get("id")
    account.new_api_name = (channel.get("name") or "").strip() or None
    account.new_api_tag = channel.get("tag") or KIMI_POOL_TAG
    account.new_api_weight = _as_int(channel.get("weight"))
    account.new_api_priority = _as_int(channel.get("priority"))
    account.new_api_status_o1 = _channel_status(channel.get("status"))
    recompute_overall_status(account)
    await session.commit()


async def ensure_kimi_newapi_channels(
    session: AsyncSession,
    results: list[KimiDeployResult],
    accounts: list[dict[str, str]],
    *,
    priority: int = DEFAULT_PRIORITY,
    weight: int = DEFAULT_WEIGHT,
    only_ok: bool = True,
) -> None:
    """Create a Kimi pool channel for each result that is not already in NewAPI."""
    await attach_kimi_newapi_status(session, results, accounts)
    try:
        gateway = kimi_pool_gateway()
    except NewApiError as exc:
        for result in results:
            if not result.new_api_present:
                result.new_api_error = str(exc)
        return

    async with _gateway_lock:
        channels = await list_kimi_pool_channels(force=True)
        next_index = next_kimi_index(channels)
        for index, result in enumerate(results):
            account = accounts[index] if index < len(accounts) else {}
            if only_ok and not result.ok:
                continue
            row_priority, row_weight = _routing_for_account(
                account, default_priority=priority, default_weight=weight
            )
            resource_name = (result.account_name or account.get("account_name") or "").strip()
            endpoint = result.azure_openai_endpoint or account.get("azure_openai_endpoint")
            subscription_id = (result.subscription_id or account.get("AZURE_SUBSCRIPTION_ID") or "").strip()
            hosts = await _hosts_for(session, result, account)
            if not resource_name:
                resource_name = next(iter(hosts), "")
            existing = match_channel(channels, hosts)
            if existing is not None:
                _apply_channel(result, existing)
                continue
            if not resource_name or not subscription_id:
                if result.ok:
                    result.new_api_error = "Missing Foundry resource to add a NewAPI channel."
                continue
            try:
                base_url = openai_base_url(endpoint, resource_name)
                api_key = await decrypt_foundry_key(session, subscription_id, resource_name)
                if not api_key:
                    result.new_api_error = "No stored Foundry API key. Deploy or test the model first."
                    continue
                custom = (account.get("new_api_name") or "").strip()
                created = None
                last_error = None
                if custom:
                    name = clean_channel_name(custom)
                    taken = _name_owner(channels, name)
                    if taken is not None:
                        result.new_api_error = f"O1 already has a channel named {name}."
                        continue
                    await _post_channel(
                        gateway,
                        _channel_create_body(
                            name=name,
                            api_key=api_key,
                            base_url=base_url,
                            priority=row_priority,
                            weight=row_weight,
                        ),
                    )
                    created = name
                else:
                    for _attempt in range(8):
                        name = f"{KIMI_CHANNEL_PREFIX}{next_index}"
                        if _name_owner(channels, name) is not None:
                            last_error = f"O1 already has a channel named {name}."
                            next_index += 1
                            continue
                        try:
                            await _post_channel(
                                gateway,
                                _channel_create_body(
                                    name=name,
                                    api_key=api_key,
                                    base_url=base_url,
                                    priority=row_priority,
                                    weight=row_weight,
                                ),
                            )
                            created = name
                            break
                        except NewApiError as exc:
                            last_error = str(exc)
                            lowered = last_error.lower()
                            if (
                                "exist" in lowered
                                or "duplicate" in lowered
                                or "unique" in lowered
                                or "已存在" in last_error
                                or "重复" in last_error
                                or "名称" in last_error
                            ):
                                channels = await list_kimi_pool_channels(force=True)
                                next_index = next_kimi_index(channels)
                                continue
                            raise
                    if created is None:
                        raise NewApiError(last_error or "Could not allocate a NewAPI channel name.")
                invalidate_kimi_pool_cache()
                channels = await list_kimi_pool_channels(force=True)
                next_index = next_kimi_index(channels)
                channel = match_channel(channels, {resource_key(resource_name), resource_key(base_url)})
                if channel is None:
                    channel = next((item for item in channels if item.get("name") == created), None) or {
                        "name": created,
                        "status": 1,
                        "priority": row_priority,
                        "weight": row_weight,
                        "tag": KIMI_POOL_TAG,
                        "base_url": base_url,
                    }
                _apply_channel(result, channel, created=True)
                try:
                    await _stamp_portal_account(session, subscription_id, resource_name, channel)
                except Exception:
                    logger.exception("Could not stamp portal account after NewAPI create for %s", resource_name)
            except Exception as exc:  # noqa: BLE001
                logger.warning("NewAPI channel create failed for %s: %s", resource_name, exc)
                result.new_api_error = str(exc)[:300]


def _channel_update_body(
    channel: dict,
    name: str,
    *,
    priority: int | None = None,
    weight: int | None = None,
) -> dict[str, Any]:
    """Name/routing update. Omit key so NewAPI keeps the existing key."""
    body: dict[str, Any] = {
        "id": channel.get("id"),
        "type": channel.get("type") or KIMI_CHANNEL_TYPE,
        "name": name,
        "base_url": channel.get("base_url"),
        "other": channel.get("other") or KIMI_AZURE_API_VERSION,
        "models": channel.get("models") or KIMI_CHANNEL_MODELS,
        "group": channel.get("group") or KIMI_CHANNEL_GROUP,
        "tag": KIMI_POOL_TAG,
        "model_mapping": channel.get("model_mapping") or KIMI_MODEL_MAPPING,
        "setting": channel.get("setting") or KIMI_SETTING,
        "settings": channel.get("settings") or KIMI_SETTINGS,
        "param_override": channel.get("param_override") or KIMI_PARAM_OVERRIDE,
        "openai_organization": channel.get("openai_organization") or "",
        "test_model": channel.get("test_model") or "",
        "status_code_mapping": channel.get("status_code_mapping") or "",
        "header_override": channel.get("header_override") or "",
        "remark": channel.get("remark") or "",
    }
    pri = channel.get("priority") if priority is None else priority
    wt = channel.get("weight") if weight is None else weight
    if pri is not None:
        body["priority"] = pri
    if wt is not None:
        body["weight"] = wt
    if channel.get("status") is not None:
        body["status"] = channel.get("status")
    if channel.get("auto_ban") is not None:
        body["auto_ban"] = channel.get("auto_ban")
    return body


async def _put_channel(gateway: Gateway, body: dict[str, Any]) -> None:
    if gateway.label != KIMI_NEWAPI_GATEWAY:
        raise NewApiError("Kimi channels are only updated on O1.")
    async with httpx.AsyncClient(timeout=30, proxy=gateway.proxy) as client:
        response = await client.put(
            f"{gateway.base_url}/api/channel/",
            headers=_headers(gateway),
            json=body,
        )
    try:
        payload = response.json()
    except ValueError as exc:
        if is_newapi_auth_failure(response.status_code, text=response.text[:200]):
            raise NewApiAuthError("O1", response.status_code, response.text[:200]) from exc
        raise NewApiError(f"O1 channel update returned non-JSON ({response.status_code})") from exc
    _raise_for_channel_write(response, payload if isinstance(payload, dict) else {}, "update")


async def rename_kimi_newapi_channel(
    session: AsyncSession,
    *,
    name: str = "",
    priority: int | None = None,
    weight: int | None = None,
    channel_id: int | None = None,
    subscription_id: str = "",
    resource_name: str = "",
    endpoint: str | None = None,
) -> KimiDeployResult:
    """Update name/priority/weight on an existing O1 channel. Does not touch O2."""
    result = KimiDeployResult(
        ok=True,
        subscription_id=subscription_id or None,
        account_name=resource_name or None,
        azure_openai_endpoint=endpoint,
    )
    try:
        wanted_name = clean_channel_name(name) if name.strip() else ""
        if priority is not None:
            priority = _clamp_int(priority, 0, 10000)
        if weight is not None:
            weight = _clamp_int(weight, 1, 10000)
        gateway = kimi_pool_gateway()
    except NewApiError as exc:
        result.ok = False
        result.new_api_error = str(exc)
        return result

    hosts = {key for key in (resource_key(resource_name), resource_key(endpoint)) if key}
    async with _gateway_lock:
        try:
            channels = await list_kimi_pool_channels(force=True)
            channel = None
            if channel_id is not None:
                channel = next((item for item in channels if _as_int(item.get("id")) == channel_id), None)
            if channel is None and hosts:
                channel = match_channel(channels, hosts)
            if channel is None:
                raise NewApiError("No O1 NewAPI channel found for this deployment.")
            host = _host_key(channel.get("base_url"))
            if hosts and host and host not in hosts:
                raise NewApiError("That NewAPI channel does not match this Foundry resource.")
            current_name = (channel.get("name") or "").strip()
            target_name = wanted_name or current_name
            if not target_name:
                raise NewApiError("Missing NewAPI channel name.")
            current_priority = _as_int(channel.get("priority"))
            current_weight = _as_int(channel.get("weight"))
            target_priority = current_priority if priority is None else priority
            target_weight = current_weight if weight is None else weight
            if (
                current_name == target_name
                and current_priority == target_priority
                and current_weight == target_weight
            ):
                _apply_channel(result, channel)
                return result
            if target_name != current_name:
                taken = _name_owner(channels, target_name, ignore_id=_as_int(channel.get("id")))
                if taken is not None:
                    raise NewApiError(f"O1 already has a channel named {target_name}.")
            await _put_channel(
                gateway,
                _channel_update_body(
                    channel,
                    target_name,
                    priority=target_priority,
                    weight=target_weight,
                ),
            )
            invalidate_kimi_pool_cache()
            channels = await list_kimi_pool_channels(force=True)
            updated = next((item for item in channels if _as_int(item.get("id")) == _as_int(channel.get("id"))), None)
            if updated is None:
                channel = {**channel, "name": target_name, "priority": target_priority, "weight": target_weight}
            else:
                channel = updated
            _apply_channel(result, channel)
            if subscription_id and resource_name:
                try:
                    await _stamp_portal_account(session, subscription_id, resource_name, channel)
                except Exception:
                    logger.exception("Could not stamp portal account after NewAPI update for %s", resource_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("NewAPI channel update failed: %s", exc)
            result.ok = False
            result.new_api_error = str(exc)[:300]
    if result.ok:
        from app.services.google_sheet_inventory import sync_deploy_results

        await sync_deploy_results([result])
    return result
