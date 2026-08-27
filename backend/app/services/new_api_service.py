"""Clients for the NewAPI gateways.

Two portals are supported (O1 and O2). Base URLs, tokens, and optional tag
filters come from environment settings. Auth for both: a non-expiring system
access token sent as "Authorization: Bearer" together with the "New-Api-User"
header. A portal with a tag filter only tracks channels carrying that tag.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.provider_account import ProviderAccount
from app.repositories.account_repository import AccountRepository

logger = logging.getLogger(__name__)

# Serializes channel fetch/write against enable/disable so an in-flight sync
# cannot overwrite a just-toggled status (or vice versa).
_gateway_lock = asyncio.Lock()


class NewApiError(RuntimeError):
    pass


class NewApiAuthError(NewApiError):
    def __init__(self, label: str, status: int | None = None, detail: str = "") -> None:
        self.label = label
        self.status = status
        suffix = f" ({status})" if status else ""
        extra = f": {detail}" if detail else ""
        super().__init__(f"{label} NewAPI token unauthorized{suffix}{extra}")


def is_newapi_auth_failure(status: int | None, body: dict | None = None, text: str = "") -> bool:
    if status in {401, 403}:
        return True
    message = " ".join(
        part
        for part in (
            str((body or {}).get("message") or ""),
            str((body or {}).get("msg") or ""),
            text,
        )
        if part
    ).lower()
    hints = (
        "unauthor",
        "token is invalid",
        "access token is invalid",
        "token expired",
        "expired token",
        "not login",
        "未登录",
        "无权",
        "认证失败",
    )
    return any(hint in message for hint in hints)


@dataclass(frozen=True)
class Gateway:
    label: str
    base_url: str
    token: str
    user_id: int
    tag_filter: str | None = None
    proxy: str | None = None


def gateways() -> list[Gateway]:
    settings = get_settings()
    result: list[Gateway] = []
    if settings.new_api_system_token:
        result.append(
            Gateway("O1", settings.new_api_base_url.rstrip("/"), settings.new_api_system_token, settings.new_api_user_id)
        )
    if settings.new_api2_system_token:
        result.append(
            Gateway(
                "O2",
                settings.new_api2_base_url.rstrip("/"),
                settings.new_api2_system_token,
                settings.new_api2_user_id,
                tag_filter=settings.new_api2_tag_filter or None,
                proxy=settings.new_api2_proxy or None,
            )
        )
    if not result:
        raise NewApiError("No NewAPI system token configured (set NEW_API_SYSTEM_TOKEN / NEW_API2_SYSTEM_TOKEN)")
    return result


def _headers(gateway: Gateway) -> dict[str, str]:
    return {
        "accept": "application/json",
        "authorization": f"Bearer {gateway.token}",
        "new-api-user": str(gateway.user_id),
        "cache-control": "no-store",
    }


def _quota_to_usd(quota: float) -> float:
    unit = get_settings().new_api_quota_per_unit or 1
    if unit <= 0:
        unit = 1
    return quota / unit


def _host_key(url: str | None) -> str | None:
    """Channels use <resource>.openai.azure.com while portal accounts store
    <resource>.cognitiveservices.azure.com — the first host label is the Azure
    resource name and is the reliable join key.
    """
    if not url:
        return None
    host = urlparse(url if "//" in url else f"https://{url}").hostname
    return host.split(".")[0].lower() if host else None


def _account_key(account: ProviderAccount) -> str | None:
    name = (account.resource_name or "").strip().lower()
    return name or _host_key(account.endpoint)


def _index_accounts(accounts: list[ProviderAccount]) -> dict[str, ProviderAccount | None]:
    """Map match-key → account. Duplicate keys are stored as None so neither
    colliding account silently steals the other's channels.
    """
    by_key: dict[str, ProviderAccount | None] = {}
    for account in accounts:
        key = _account_key(account)
        if not key:
            continue
        if key in by_key:
            other = by_key[key]
            logger.warning(
                "Duplicate NewAPI match key %s: %s vs %s — skipping both",
                key,
                other.name if other else "?",
                account.name,
            )
            by_key[key] = None
        else:
            by_key[key] = account
    return by_key


def _membership(account: ProviderAccount) -> set[str]:
    return {part for part in (account.new_api_gateway or "").split("+") if part in {"O1", "O2"}}


def _channel_status(raw) -> int | None:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _portal_status(channels: list[dict]) -> int | None:
    """On if any channel is enabled (1). Any other known NewAPI status
    (2 = manual disable, 3 = auto-disable) is off.
    """
    known = [_channel_status(channel.get("status")) for channel in channels]
    known = [status for status in known if status is not None]
    if not known:
        return None
    return 1 if any(status == 1 for status in known) else 2


def _set_portal_cost_status(account: ProviderAccount, label: str, quota: float, status: int | None) -> None:
    cost = _quota_to_usd(quota)
    if label == "O1":
        account.new_api_cost_o1_usd = cost
        account.new_api_status_o1 = status
    elif label == "O2":
        account.new_api_cost_o2_usd = cost
        account.new_api_status_o2 = status


def _clear_portal(account: ProviderAccount, label: str) -> None:
    _set_portal_cost_status(account, label, 0.0, None)


def recompute_overall_status(account: ProviderAccount) -> None:
    """Enabled while any mapped portal is enabled or unknown. Unknown stays
    live so a failed fetch cannot mark an exhausted account 'off' and stop
    auto-disable retries. Auto-disabled (3) is treated as off.
    """
    labels = _membership(account)
    statuses: list[int | None] = []
    if "O1" in labels:
        statuses.append(account.new_api_status_o1)
    if "O2" in labels:
        statuses.append(account.new_api_status_o2)
    if not statuses:
        account.new_api_status = None
        return
    account.new_api_status = 1 if any(status is None or status == 1 for status in statuses) else 2


def gateway_still_live(account: ProviderAccount) -> bool:
    recompute_overall_status(account)
    return account.new_api_status == 1


def _recompute_totals(account: ProviderAccount) -> None:
    labels = _membership(account)
    o1 = account.new_api_cost_o1_usd or 0.0 if "O1" in labels else 0.0
    o2 = account.new_api_cost_o2_usd or 0.0 if "O2" in labels else 0.0
    if "O1" not in labels:
        account.new_api_cost_o1_usd = None
    if "O2" not in labels:
        account.new_api_cost_o2_usd = None
    account.new_api_cost_usd = (o1 + o2) if labels else None
    account.new_api_used_quota = None
    recompute_overall_status(account)


async def fetch_channels(gateway: Gateway) -> list[dict]:
    items: list[dict] = []
    page = 1
    page_size = 100
    async with httpx.AsyncClient(timeout=30, proxy=gateway.proxy) as client:
        while True:
            response = await client.get(
                f"{gateway.base_url}/api/channel/",
                params={"tag_mode": "false", "id_sort": "false", "p": page, "page_size": page_size},
                headers=_headers(gateway),
            )
            try:
                body = response.json()
            except ValueError as exc:
                if is_newapi_auth_failure(response.status_code, text=response.text[:200]):
                    raise NewApiAuthError(gateway.label, response.status_code, response.text[:200]) from exc
                raise NewApiError(f"{gateway.label} channel list returned non-JSON ({response.status_code})") from exc
            if is_newapi_auth_failure(response.status_code, body, response.text[:200]):
                raise NewApiAuthError(gateway.label, response.status_code, str(body.get("message") or response.text)[:200])
            if response.status_code != 200 or body.get("success") is False:
                raise NewApiError(
                    f"{gateway.label} channel list failed ({response.status_code}): {response.text[:200]}"
                )
            data = body.get("data") or {}
            if isinstance(data, list):
                batch = data
                total = None
            else:
                batch = data.get("items") or []
                total = data.get("total")
            if not batch:
                break
            items.extend(batch)
            if total is not None:
                try:
                    if page * page_size >= int(total):
                        break
                except (TypeError, ValueError):
                    pass
            if len(batch) < page_size:
                break
            page += 1
    if gateway.tag_filter:
        wanted = gateway.tag_filter.strip().lower()
        items = [ch for ch in items if (ch.get("tag") or "").strip().lower() == wanted]
    return items


async def set_channel_status(gateway: Gateway, channel_id: int, status: int) -> bool:
    """Flips a gateway channel's status (1 = enabled, 2 = manually disabled).
    Returns whether the gateway reported a change.
    """
    async with httpx.AsyncClient(timeout=20, proxy=gateway.proxy) as client:
        response = await client.post(
            f"{gateway.base_url}/api/channel/{channel_id}/status",
            headers=_headers(gateway),
            json={"status": status},
        )
    try:
        body = response.json()
    except ValueError as exc:
        if is_newapi_auth_failure(response.status_code, text=response.text[:200]):
            raise NewApiAuthError(gateway.label, response.status_code, response.text[:200]) from exc
        raise NewApiError(f"{gateway.label} status update returned non-JSON ({response.status_code})") from exc
    if is_newapi_auth_failure(response.status_code, body, response.text[:200]):
        raise NewApiAuthError(gateway.label, response.status_code, str(body.get("message") or response.text)[:200])
    if response.status_code != 200 or body.get("success") is False:
        raise NewApiError(f"{gateway.label} status update failed ({response.status_code}): {response.text[:200]}")
    return bool(body.get("data"))


async def set_gateway_status(
    session: AsyncSession, account_id: int, status: int, gateway_label: str | None = None
) -> dict:
    """Flips every channel for this account's Azure resource. By default acts on
    portals this account is already mapped to (O1, O2, or both). Pass
    gateway_label="O1"/"O2" to touch only that portal.
    """
    async with _gateway_lock:
        return await _set_gateway_status_locked(session, account_id, status, gateway_label)


async def _set_gateway_status_locked(
    session: AsyncSession, account_id: int, status: int, gateway_label: str | None
) -> dict:
    account = await AccountRepository(session).get(account_id)
    if account is None:
        raise NewApiError("Account not found")
    key = _account_key(account)
    if not key:
        raise NewApiError("Account has no endpoint to match against")
    membership = _membership(account)
    targets = [gw for gw in gateways() if gateway_label is None or gw.label == gateway_label]
    if gateway_label is None and membership:
        targets = [gw for gw in targets if gw.label in membership]
    if not targets:
        raise NewApiError(f"Gateway {gateway_label} is not configured")

    flipped: dict[str, list[int]] = {}
    errors: dict[str, str] = {}
    for gateway in targets:
        try:
            channels = await fetch_channels(gateway)
            matches = [channel for channel in channels if _host_key(channel.get("base_url")) == key]
            if not matches:
                if membership and gateway.label not in membership:
                    continue
                errors[gateway.label] = "no matching channels"
                continue
            changed: list[int] = []
            failed: list[str] = []
            for channel in matches:
                channel_id = channel.get("id")
                try:
                    await set_channel_status(gateway, int(channel_id), status)
                    changed.append(int(channel_id))
                except Exception as exc:  # noqa: BLE001 - keep flipping siblings
                    failed.append(f"{channel_id}: {exc}")
            if failed:
                errors[gateway.label] = "; ".join(failed)[:200]
            if changed and not failed:
                flipped[gateway.label] = changed
                if gateway.label == "O1":
                    account.new_api_status_o1 = status
                else:
                    account.new_api_status_o2 = status
            elif changed:
                flipped[gateway.label] = changed
        except Exception as exc:  # noqa: BLE001 - try the other gateway regardless
            logger.warning("Status update failed on %s for %s", gateway.label, account.name, exc_info=True)
            errors[gateway.label] = str(exc)[:200]

    if not flipped and errors:
        scope = gateway_label or "any gateway"
        raise NewApiError(f"No matching channels updated on {scope} ({'; '.join(errors.values())})")
    if not flipped:
        raise NewApiError(f"No matching channels found on {gateway_label or 'any gateway'}")

    recompute_overall_status(account)
    await session.commit()
    return {"status": "ok" if not errors else "partial", "flipped": flipped, "errors": errors}


async def sync_new_api(session: AsyncSession) -> dict:
    """Pulls channels from every configured gateway and writes usage onto the
    matching portal accounts. A portal that fails to fetch is left untouched
    so an O2 outage cannot zero stored O2 spend or drop O2 membership.
    """
    async with _gateway_lock:
        return await _sync_new_api_locked(session)


async def _sync_new_api_locked(session: AsyncSession) -> dict:
    accounts = await AccountRepository(session).list_all()
    by_key = _index_accounts(accounts)
    now = datetime.now(timezone.utc)
    total_channels = 0
    unmatched: dict[str, list[str]] = {}
    matched: dict[int, list[tuple[str, dict]]] = {}
    errors: dict[str, str] = {}
    fetched_ok: set[str] = set()

    for gateway in gateways():
        try:
            channels = await fetch_channels(gateway)
        except Exception as exc:  # noqa: BLE001 - one bad gateway must not block the other
            logger.warning("Channel fetch failed for %s", gateway.label, exc_info=True)
            errors[gateway.label] = str(exc)[:200]
            continue
        fetched_ok.add(gateway.label)
        total_channels += len(channels)
        for channel in channels:
            key = _host_key(channel.get("base_url"))
            account = by_key.get(key) if key else None
            if key and key in by_key and account is None:
                unmatched.setdefault(gateway.label, []).append(
                    f"{channel.get('name') or channel.get('id')} (duplicate key {key})"
                )
                continue
            if account is None:
                unmatched.setdefault(gateway.label, []).append(channel.get("name") or str(channel.get("id")))
                continue
            matched.setdefault(account.id, []).append((gateway.label, channel))

    on_both = 0
    for account in accounts:
        labelled = matched.get(account.id, [])
        quota_by_label: dict[str, float] = {}
        channels_by_label: dict[str, list[dict]] = {}
        for label, channel in labelled:
            quota_by_label[label] = quota_by_label.get(label, 0.0) + float(channel.get("used_quota") or 0)
            channels_by_label.setdefault(label, []).append(channel)

        membership = _membership(account)
        for label in fetched_ok:
            if label in quota_by_label:
                membership.add(label)
                _set_portal_cost_status(
                    account, label, quota_by_label[label], _portal_status(channels_by_label[label])
                )
            else:
                membership.discard(label)
                _clear_portal(account, label)
        account.new_api_gateway = "+".join(sorted(membership)) or None
        _recompute_totals(account)

        fetched_channels = [channel for label, channel in labelled if label in fetched_ok]
        if fetched_channels:
            existing_id = account.new_api_channel_id
            primary = next((ch for ch in fetched_channels if ch.get("id") == existing_id), None)
            if primary is None:
                # Prefer O1 when both are present so identity does not flip with quota.
                o1_channels = channels_by_label.get("O1") or []
                pool = o1_channels or fetched_channels
                primary = max(pool, key=lambda ch: float(ch.get("used_quota") or 0))
            account.new_api_channel_id = primary.get("id")
            account.new_api_name = (primary.get("name") or "").strip() or None
            account.new_api_tag = (primary.get("tag") or "").strip() or None
            account.new_api_weight = primary.get("weight")
            account.new_api_priority = primary.get("priority")
            account.new_api_synced_at = now
        elif fetched_ok and not membership:
            account.new_api_channel_id = None
            account.new_api_name = None
            account.new_api_tag = None
            account.new_api_synced_at = now
        if len(membership) > 1:
            on_both += 1

    await session.commit()
    logger.info(
        "NewAPI sync: %d channels, %d accounts matched (%d on both portals), fetched=%s unmatched=%s errors=%s",
        total_channels,
        len(matched),
        on_both,
        sorted(fetched_ok),
        {k: len(v) for k, v in unmatched.items()},
        list(errors),
    )
    return {
        "status": "ok" if not errors else "partial",
        "channels": total_channels,
        "accounts_matched": len(matched),
        "accounts_on_both": on_both,
        "unmatched_channels": unmatched,
        "gateway_errors": errors,
        "synced_at": now.isoformat(),
    }
