from __future__ import annotations

import asyncio
import logging

from redis.exceptions import RedisError

from app.services.azure_inventory_cache import (
    _client_or_none,
    remember_host_quota,
    remember_host_quotas,
    remembered_host_quotas,
)
from app.services.kimi_newapi import _channel_status, _host_key, is_kimi_pool_channel, list_kimi_pool_channels

logger = logging.getLogger(__name__)

LIVE_SET = "kimi:capacity:live"
LIVE_READY = "kimi:capacity:live:ready"
LIVE_LOCK = "kimi:capacity:lock"
LIVE_TTL_S = 6 * 60 * 60
LOCK_TTL_S = 60

_refresh_lock = asyncio.Lock()


def live_newapi_hosts(channels: list[dict]) -> list[str]:
    hosts: list[str] = []
    seen: set[str] = set()
    for channel in channels:
        if not is_kimi_pool_channel(channel):
            continue
        if _channel_status(channel.get("status")) != 1:
            continue
        host = _host_key(channel.get("base_url"))
        if not host or host in seen:
            continue
        seen.add(host)
        hosts.append(host)
    return hosts


def rpm_from_tpm(tpm: int) -> int:
    return tpm // 1000 if tpm >= 1000 and tpm % 1000 == 0 else tpm


def sum_live_capacity(hosts: list[str], quotas: dict[str, tuple[int, int]]) -> dict[str, int]:
    tpm = 0
    rpm = 0
    accounts = 0
    for host in hosts:
        pair = quotas.get(host)
        if pair is None:
            continue
        tokens, requests = pair
        if tokens <= 0:
            continue
        tpm += tokens
        rpm += requests
        accounts += 1
    return {"tpm": tpm, "rpm": rpm, "accounts": accounts}


def _empty() -> dict[str, int]:
    return {"tpm": 0, "rpm": 0, "accounts": 0}


async def _live_hosts() -> list[str]:
    client = _client_or_none()
    if client is None:
        return []
    try:
        raw = await client.smembers(LIVE_SET)
    except RedisError as exc:
        logger.warning("Capacity live-set read failed: %s", exc)
        return []
    return sorted({str(host).strip().lower() for host in (raw or []) if str(host).strip()})


async def _ready() -> bool:
    client = _client_or_none()
    if client is None:
        return False
    try:
        return bool(await client.exists(LIVE_READY))
    except RedisError as exc:
        logger.warning("Capacity ready-flag read failed: %s", exc)
        return False


async def _replace_live_set(hosts: list[str]) -> None:
    client = _client_or_none()
    if client is None:
        return
    pipe = client.pipeline(transaction=True)
    pipe.delete(LIVE_SET)
    if hosts:
        pipe.sadd(LIVE_SET, *hosts)
    pipe.set(LIVE_READY, "1", ex=LIVE_TTL_S)
    try:
        await pipe.execute()
    except RedisError:
        logger.exception("Capacity live-set write failed")


async def _drop_ready() -> None:
    client = _client_or_none()
    if client is None:
        return
    try:
        await client.delete(LIVE_READY)
    except RedisError as exc:
        logger.warning("Capacity ready-flag delete failed: %s", exc)


async def _fill_missing_quotas(hosts: list[str]) -> dict[str, tuple[int, int]]:
    quotas = await remembered_host_quotas()
    missing = [host for host in hosts if host not in quotas]
    if not missing:
        return quotas
    from app.services.google_sheet_inventory import tpm_by_host

    try:
        sheet = await asyncio.to_thread(tpm_by_host)
    except Exception:
        logger.exception("Capacity sheet read failed")
        return quotas
    found: dict[str, tuple[int, int]] = {}
    for host in missing:
        tokens = sheet.get(host)
        if not tokens:
            continue
        found[host] = (tokens, rpm_from_tpm(tokens))
    if found:
        quotas.update(found)
        await remember_host_quotas(found)
    return quotas


async def _compute_from_newapi() -> tuple[list[str], dict[str, tuple[int, int]]] | None:
    try:
        channels = await list_kimi_pool_channels()
    except Exception:
        logger.exception("Capacity recompute: NewAPI pool failed")
        return None
    hosts = live_newapi_hosts(channels)
    if not hosts:
        previous = await _live_hosts()
        if previous:
            logger.warning("Capacity recompute saw 0 live K3 hosts; keeping the previous set")
            return previous, await remembered_host_quotas()
        return [], {}
    quotas = await _fill_missing_quotas(hosts)
    return hosts, quotas


async def refresh_live_set() -> dict[str, int]:
    async with _refresh_lock:
        client = _client_or_none()
        locked = False
        if client is not None:
            try:
                locked = bool(await client.set(LIVE_LOCK, "1", nx=True, ex=LOCK_TTL_S))
            except RedisError as exc:
                logger.warning("Capacity lock failed: %s", exc)
                locked = True
            if not locked:
                for _ in range(24):
                    await asyncio.sleep(0.25)
                    if await _ready():
                        live = await _live_hosts()
                        quotas = await remembered_host_quotas()
                        return sum_live_capacity(live, quotas)
        try:
            computed = await _compute_from_newapi()
            if computed is None:
                live = await _live_hosts()
                quotas = await remembered_host_quotas()
                return sum_live_capacity(live, quotas) if live else _empty()
            hosts, quotas = computed
            await _replace_live_set(hosts)
            return sum_live_capacity(hosts, quotas)
        finally:
            if locked and client is not None:
                try:
                    await client.delete(LIVE_LOCK)
                except RedisError:
                    logger.debug("Capacity lock release failed", exc_info=True)


async def active_newapi_capacity() -> dict[str, int]:
    if await _ready():
        live = await _live_hosts()
        quotas = await remembered_host_quotas()
        return sum_live_capacity(live, quotas)
    return await refresh_live_set()


async def set_host_live(host: str | None, live: bool, tpm: int | None = None, rpm: int | None = None) -> None:
    key = (host or "").strip().lower()
    if not key:
        return
    client = _client_or_none()
    if client is None or not await _ready():
        if tpm:
            await remember_host_quota(key, int(tpm), int(rpm or rpm_from_tpm(int(tpm))))
        return
    try:
        if live:
            if tpm:
                await remember_host_quota(key, int(tpm), int(rpm or rpm_from_tpm(int(tpm))))
            elif key not in await remembered_host_quotas():
                await _drop_ready()
                return
            await client.sadd(LIVE_SET, key)
        else:
            await client.srem(LIVE_SET, key)
    except RedisError:
        logger.exception("Capacity live-set patch failed for %s", key)
        await _drop_ready()


async def warmup_capacity() -> None:
    try:
        await refresh_live_set()
    except Exception:
        logger.exception("Capacity warmup failed")
