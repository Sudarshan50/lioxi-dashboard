"""Deploy the GPT stack onto an existing 10k Foundry account.

Models are created on the account's current Azure resource at the highest
TPM/RPM the subscription quota allows. A matching O1 channel is created or
updated in the gpt-astra pool and pointed at that same resource, so NewAPI
spend rolls into the account that already owns the Kimi channel. The stop
limit disables every channel on that resource together.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import get_secret_box
from app.core.exceptions import AzureApiError
from app.database import SessionLocal
from app.models.gpt_deploy_log import GptDeployLog
from app.models.provider_account import ProviderAccount
from app.providers.azure.arm_client import AzureArmClient
from app.providers.azure.token_provider import AzureTokenProvider
from app.providers.base import ProviderCredentials
from app.runtime import AZURE_SYNC_CONCURRENCY
from app.services.account_group_service import is_10k_account
from app.services.alert_service import credits_exhausted, credit_grant_usd, get_alert_config, new_api_spend, stop_at_usd
from app.services.deploy_defaults import get_deploy_defaults
from app.services.gpt_catalog import (
    GPT_AZURE_API_VERSION,
    GPT_CHANNEL_GROUP,
    GPT_CHANNEL_PREFIX,
    GPT_CHANNEL_RE,
    GPT_CHANNEL_TAG,
    GPT_CHANNEL_TYPE,
    GPT_GROUP_PATCHES,
    GPT_DEPLOY_API_VERSION,
    GPT_MODELS_API_VERSION,
    GPT_MODEL_NAMES,
    GPT_PARAM_OVERRIDE,
    GPT_PRICE_PATCHES,
    GPT_STACK_ID,
    GPT_USAGES_API_VERSION,
    deployment_name,
    index_existing_deployments,
    merge_model_list,
    merge_price_maps,
    next_gpt_channel_name,
    plan_model,
    rates_from_deployment,
    resolve_requested_models,
    select_catalog_model,
)
from app.services.kimi_newapi import openai_base_url
from app.services.new_api_service import (
    Gateway,
    NewApiAuthError,
    NewApiError,
    _gateway_lock,
    _headers,
    _host_key,
    fetch_channels,
    gateways,
    is_newapi_auth_failure,
    set_channel_status,
)
from app.services.openai_key_store import decrypt_foundry_key

logger = logging.getLogger(__name__)

# Same setting blob as the live O1 channel gpt-astra-proxy1.
GPT_SETTING = json.dumps(
    {
        "force_format": False,
        "thinking_to_content": False,
        "force_stream_upstream": False,
        "proxy": "",
        "pass_through_body_enabled": False,
        "responses_websocket_enabled": False,
        "system_prompt": "",
        "system_prompt_override": False,
    },
    separators=(",", ":"),
)
GPT_SETTINGS = json.dumps({"disable_task_polling_sleep": False}, separators=(",", ":"))

_arm: AzureArmClient | None = None
_job_lock = asyncio.Lock()
_job: dict[str, Any] | None = None


class GptDeployError(Exception):
    pass


def _arm_client() -> AzureArmClient:
    global _arm
    if _arm is None:
        _arm = AzureArmClient(AzureTokenProvider())
    return _arm


def eligibility_error(account: ProviderAccount) -> str | None:
    if account.blocked:
        return "This account is blocked."
    if not is_10k_account(account):
        return "GPT deploy is only available on 10k accounts."
    return None


def bulk_workers(count: int) -> int:
    """How many accounts deploy at once. Matches the Azure request cap."""
    return max(1, min(AZURE_SYNC_CONCURRENCY, max(count, 1)))


def job_snapshot() -> dict[str, Any]:
    if _job is None:
        return {"running": False, "total": 0, "done": 0, "failed": 0, "skipped": []}
    return {
        "running": bool(_job.get("running")),
        "job_id": _job.get("job_id"),
        "account_id": _job.get("account_id"),
        "account_name": _job.get("account_name"),
        "action": _job.get("action"),
        "error": _job.get("error"),
        "started_at": _job.get("started_at"),
        "finished_at": _job.get("finished_at"),
        "total": int(_job.get("total") or 0),
        "done": int(_job.get("done") or 0),
        "failed": int(_job.get("failed") or 0),
        "current": _job.get("current"),
        "skipped": list(_job.get("skipped") or []),
    }


def _is_gpt_pool_channel(channel: dict) -> bool:
    tag = str(channel.get("tag") or "").strip().lower()
    name = str(channel.get("name") or "")
    return tag == GPT_CHANNEL_TAG or name.lower().startswith(GPT_CHANNEL_PREFIX)


def gpt_channels_by_host(channels: list[dict]) -> dict[str, str]:
    """Azure resource name → gpt-astra channel name."""
    found: dict[str, str] = {}
    for channel in channels:
        if not _is_gpt_pool_channel(channel):
            continue
        host = _host_key(channel.get("base_url"))
        name = str(channel.get("name") or "").strip()
        if host and name and host not in found:
            found[host] = name
    return found


def gpt_deployed_hosts(channels: list[dict]) -> set[str]:
    """Azure resource names that already have a gpt-astra channel."""
    return set(gpt_channels_by_host(channels))


def _account_row(account: ProviderAccount, buffer: float, deployed_hosts: set[str] | None = None) -> dict[str, Any]:
    host = (account.resource_name or "").strip().lower()
    return {
        "id": account.id,
        "name": account.name,
        "owner_tag": account.owner_tag or "",
        "location": account.location or "",
        "resource_name": account.resource_name or "",
        "new_api_name": account.new_api_name or "",
        "new_api_status": account.new_api_status,
        "blocked": bool(account.blocked),
        "gpt_deployed": bool(host and host in (deployed_hosts or set())),
        "spend_usd": new_api_spend(account),
        "grant_usd": credit_grant_usd(account),
        "stop_at_usd": stop_at_usd(account, buffer),
    }


async def _deployed_hosts() -> set[str]:
    try:
        return gpt_deployed_hosts(await fetch_channels(_o1_gateway()))
    except (NewApiError, NewApiAuthError):
        logger.warning("Could not read gpt-astra channels for the account list", exc_info=True)
        return set()


async def list_gpt_accounts(session: AsyncSession) -> list[dict[str, Any]]:
    config = await get_alert_config(session)
    buffer = float(config["overspend_buffer_usd"])
    deployed_hosts = await _deployed_hosts()
    rows = (await session.execute(select(ProviderAccount))).scalars().all()
    accounts = [_account_row(row, buffer, deployed_hosts) for row in rows if is_10k_account(row)]
    accounts.sort(key=lambda row: (row["name"] or "").lower())
    return accounts


def _credentials(account: ProviderAccount) -> ProviderCredentials:
    secret = get_secret_box().decrypt(account.client_secret_encrypted)
    return ProviderCredentials(
        tenant_id=account.tenant_id,
        client_id=account.client_id,
        client_secret=secret,
        subscription_id=account.subscription_id,
    )


def _location(account: ProviderAccount) -> str:
    return (account.location or "eastus2").replace(" ", "").lower()


async def _catalog_and_usage(account: ProviderAccount) -> tuple[list[dict], list[dict], list[dict]]:
    credentials = _credentials(account)
    arm = _arm_client()
    location = _location(account)
    models, usages, deployments = await asyncio.gather(
        arm.get_all_pages(
            credentials,
            f"/subscriptions/{account.subscription_id}/providers/Microsoft.CognitiveServices/locations/{location}/models",
            params={"api-version": GPT_MODELS_API_VERSION},
        ),
        arm.get_all_pages(
            credentials,
            f"/subscriptions/{account.subscription_id}/providers/Microsoft.CognitiveServices/locations/{location}/usages",
            params={"api-version": GPT_USAGES_API_VERSION},
        ),
        arm.get_all_pages(
            credentials,
            f"{account.resource_id}/deployments",
            params={"api-version": GPT_DEPLOY_API_VERSION},
        ),
    )
    return models, usages, deployments


async def account_availability(session: AsyncSession, account_id: int) -> dict[str, Any]:
    account = await session.get(ProviderAccount, account_id)
    if account is None:
        raise GptDeployError("Account not found.")
    config = await get_alert_config(session)
    buffer = float(config["overspend_buffer_usd"])
    payload = _account_row(account, buffer)
    error = eligibility_error(account)
    result: dict[str, Any] = {
        "account_id": account.id,
        "account_name": account.name,
        "location": account.location or "",
        "endpoint": account.endpoint or "",
        "new_api_name": payload["new_api_name"],
        "spend_usd": payload["spend_usd"],
        "grant_usd": payload["grant_usd"],
        "stop_at_usd": payload["stop_at_usd"],
        "eligible": error is None,
        "eligibility_error": error,
        "models": [],
    }
    if error:
        return result
    try:
        catalog, usages, deployments = await _catalog_and_usage(account)
    except AzureApiError as exc:
        raise GptDeployError(str(exc)[:400]) from exc
    existing = index_existing_deployments(deployments)
    plans = []
    for name in GPT_MODEL_NAMES:
        model = select_catalog_model(catalog, name)
        plans.append(plan_model(model, usages, existing.get(name)))
    result["models"] = plans
    return result


async def list_logs(session: AsyncSession, account_id: int | None = None, limit: int = 200) -> list[GptDeployLog]:
    stmt = select(GptDeployLog).order_by(GptDeployLog.id.desc()).limit(max(1, min(limit, 500)))
    if account_id is not None:
        stmt = (
            select(GptDeployLog)
            .where(GptDeployLog.account_id == account_id)
            .order_by(GptDeployLog.id.desc())
            .limit(max(1, min(limit, 500)))
        )
    return list((await session.execute(stmt)).scalars().all())


async def _write_log(
    session: AsyncSession,
    account: ProviderAccount | None,
    action: str,
    message: str,
    *,
    level: str = "info",
) -> None:
    session.add(
        GptDeployLog(
            account_id=account.id if account is not None else None,
            account_name=account.name if account is not None else "",
            action=action[:64],
            level=level[:16],
            message=message[:2000],
            created_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()


def _scrub(text: str, secret: str) -> str:
    if secret and secret in text:
        return text.replace(secret, "***")
    return text


def _unique_ids(account_ids: list[int]) -> list[int]:
    seen: set[int] = set()
    ordered: list[int] = []
    for raw in account_ids:
        try:
            account_id = int(raw)
        except (TypeError, ValueError):
            continue
        if account_id in seen:
            continue
        seen.add(account_id)
        ordered.append(account_id)
    return ordered


async def start_gpt_deploy(account_ids: list[int], stack: bool, models: list[str]) -> dict[str, Any]:
    """Deploy one account or a batch. Accounts run together, up to the Azure cap."""
    try:
        wanted = resolve_requested_models(stack, models)
    except ValueError as exc:
        raise GptDeployError(str(exc)) from exc
    ids = _unique_ids(account_ids)
    if not ids:
        raise GptDeployError("Select at least one account.")
    ready: list[tuple[int, str]] = []
    skipped: list[str] = []
    async with SessionLocal() as session:
        for account_id in ids:
            account = await session.get(ProviderAccount, account_id)
            if account is None:
                skipped.append(f"{account_id}: account not found")
                continue
            error = eligibility_error(account)
            if error:
                skipped.append(f"{account.name}: {error}")
                continue
            ready.append((account.id, account.name))
    if not ready:
        detail = "; ".join(skipped[:8]) or "No eligible accounts."
        raise GptDeployError(detail)
    action = GPT_STACK_ID if stack or wanted == list(GPT_MODEL_NAMES) else ", ".join(wanted)
    workers = bulk_workers(len(ready))
    label = ready[0][1] if len(ready) == 1 else f"{len(ready)} accounts"
    global _job
    async with _job_lock:
        if _job and _job.get("running"):
            raise GptDeployError(
                f"A GPT deploy is already running ({_job.get('done') or 0}/{_job.get('total') or 0})."
            )
        _job = {
            "running": True,
            "job_id": uuid.uuid4().hex[:12],
            "account_id": ready[0][0] if len(ready) == 1 else None,
            "account_name": label,
            "action": action,
            "error": None,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "total": len(ready),
            "done": 0,
            "failed": 0,
            "current": "",
            "skipped": skipped,
            "workers": workers,
        }
        snapshot = job_snapshot()
    asyncio.create_task(_run_batch(ready, wanted, action, workers))
    return snapshot


async def _run_batch(
    ready: list[tuple[int, str]],
    wanted: list[str],
    action: str,
    workers: int,
) -> None:
    global _job
    inflight: set[str] = set()
    progress = asyncio.Lock()

    async with SessionLocal() as session:
        names = ", ".join(name for _id, name in ready[:12])
        extra = f" and {len(ready) - 12} more" if len(ready) > 12 else ""
        await _write_log(
            session,
            None,
            action,
            f"Bulk {action} started for {len(ready)} account(s), {workers} at a time: {names}{extra}.",
        )

    async def one(account_id: int, account_name: str) -> None:
        async with progress:
            inflight.add(account_name)
            if _job is not None:
                _job["current"] = ", ".join(sorted(inflight))
        failed = False
        try:
            async with SessionLocal() as session:
                account = await session.get(ProviderAccount, account_id)
                if account is None:
                    raise GptDeployError("Account not found.")
                await _deploy(session, account, wanted, action)
        except Exception as exc:  # noqa: BLE001
            failed = True
            logger.exception("GPT deploy failed for account %s", account_id)
            message = str(exc)[:500] or "GPT deploy failed."
            try:
                async with SessionLocal() as session:
                    account = await session.get(ProviderAccount, account_id)
                    await _write_log(session, account, action, message, level="error")
            except Exception:
                logger.exception("Could not store GPT deploy error log")
        finally:
            async with progress:
                inflight.discard(account_name)
                if _job is not None:
                    _job["done"] = int(_job.get("done") or 0) + 1
                    if failed:
                        _job["failed"] = int(_job.get("failed") or 0) + 1
                    _job["current"] = ", ".join(sorted(inflight))

    try:
        semaphore = asyncio.Semaphore(workers)

        async def bounded(account_id: int, account_name: str) -> None:
            async with semaphore:
                await one(account_id, account_name)

        await asyncio.gather(*(bounded(account_id, name) for account_id, name in ready))
        if _job is not None:
            failed = int(_job.get("failed") or 0)
            total = int(_job.get("total") or 0)
            if failed:
                _job["error"] = f"{failed} of {total} accounts failed. See the log."
    except Exception as exc:  # noqa: BLE001
        logger.exception("GPT bulk deploy failed")
        if _job is not None:
            _job["error"] = str(exc)[:500] or "GPT deploy failed."
    finally:
        if _job is not None:
            _job["running"] = False
            _job["current"] = ""
            _job["finished_at"] = datetime.now(timezone.utc).isoformat()


async def _deploy(session: AsyncSession, account: ProviderAccount, wanted: list[str], action: str) -> None:
    await _write_log(
        session,
        account,
        action,
        f"Starting {action} on {account.name} ({_location(account)}). "
        "Capacity is the max TPM/RPM Azure still has for each model.",
    )
    try:
        catalog, usages, deployments = await _catalog_and_usage(account)
    except AzureApiError as exc:
        raise GptDeployError(str(exc)[:400]) from exc
    existing = index_existing_deployments(deployments)
    deployed: list[str] = []
    secret = ""
    try:
        secret = _credentials(account).client_secret
    except Exception:
        secret = ""
    for name in wanted:
        model = select_catalog_model(catalog, name)
        plan = plan_model(model, usages, existing.get(name))
        if not plan["available"]:
            await _write_log(
                session,
                account,
                name,
                plan["reason"] or f"{name} is not available.",
                level="error",
            )
            continue
        current = existing.get(name) or {}
        already = (
            str(current.get("sku") or "") == str(plan["sku"])
            and int(current.get("capacity") or 0) >= int(plan["capacity"] or 0)
            and str(current.get("state") or "Succeeded").lower() == "succeeded"
        )
        if already:
            deployed.append(name)
            await _write_log(
                session,
                account,
                name,
                f"{name} is already at max {plan['sku']} capacity {plan['capacity']} "
                f"({plan['tpm']:,} TPM · {plan['rpm']:,} RPM).",
            )
            continue
        try:
            shown = await _put_model(account, plan)
        except Exception as exc:  # noqa: BLE001
            await _write_log(session, account, name, _scrub(str(exc), secret)[:500], level="error")
            continue
        capacity, tpm, rpm = rates_from_deployment(shown, int(plan["capacity"] or 0))
        state = str((shown.get("properties") or {}).get("provisioningState") or "")
        if state.lower() == "failed":
            await _write_log(session, account, name, f"{name} provisioning failed.", level="error")
            continue
        deployed.append(name)
        await _write_log(
            session,
            account,
            name,
            f"Deployed {name} {plan.get('version') or ''} on {plan['sku']} "
            f"capacity {capacity} · {tpm:,} TPM · {rpm:,} RPM ({state or 'submitted'}).",
        )
        # This deployment now consumes its quota. Later models use their own meters.
        existing[name] = {
            "deployment_name": name,
            "model": name,
            "sku": plan["sku"],
            "capacity": capacity,
        }

    if not deployed:
        raise GptDeployError("No models were deployed. See the log for each model.")

    await _ensure_newapi_channel(session, account, deployed, action)
    await _write_log(
        session,
        account,
        action,
        f"Finished {action} on {account.name}. Models: {', '.join(deployed)}.",
    )


async def _put_model(account: ProviderAccount, plan: dict[str, Any]) -> dict:
    name = deployment_name(str(plan["name"]))
    path = f"{account.resource_id}/deployments/{quote(name, safe='')}"
    body = {
        "sku": {"name": plan["sku"], "capacity": int(plan["capacity"])},
        "properties": {
            "model": {
                "format": "OpenAI",
                "name": name,
                "version": plan.get("version") or "",
            }
        },
    }
    credentials = _credentials(account)
    arm = _arm_client()
    shown = await arm.put(credentials, path, body, params={"api-version": GPT_DEPLOY_API_VERSION})
    return await _wait_deployment(credentials, path, shown)


async def _wait_deployment(credentials: ProviderCredentials, path: str, shown: dict) -> dict:
    state = str((shown.get("properties") or {}).get("provisioningState") or "")
    if state.lower() in {"succeeded", "failed", "canceled"}:
        return shown
    arm = _arm_client()
    last = shown
    for _ in range(36):
        await asyncio.sleep(5)
        try:
            last = await arm.get(credentials, path, params={"api-version": GPT_DEPLOY_API_VERSION})
        except AzureApiError as exc:
            if "(404)" in str(exc):
                continue
            raise
        state = str((last.get("properties") or {}).get("provisioningState") or "")
        if state.lower() in {"succeeded", "failed", "canceled"}:
            return last
    return last


def _o1_gateway() -> Gateway:
    for gateway in gateways():
        if gateway.label == "O1":
            return gateway
    raise NewApiError("O1 NewAPI is not configured (NEW_API_SYSTEM_TOKEN).")


def match_gpt_channel(channels: list[dict], host: str | None) -> dict | None:
    if not host:
        return None
    for channel in channels:
        if _host_key(channel.get("base_url")) != host:
            continue
        tag = (channel.get("tag") or "").strip().lower()
        name = str(channel.get("name") or "")
        if tag == GPT_CHANNEL_TAG or GPT_CHANNEL_RE.match(name) or name.lower().startswith("gpt-astra"):
            return channel
    return None


def desired_channel_status(account: ProviderAccount, exhausted: bool) -> int:
    """Off when the account is already stopped, so a new channel cannot outlive the limit."""
    if exhausted or account.blocked or account.new_api_status == 2:
        return 2
    return 1


async def _ensure_newapi_channel(
    session: AsyncSession,
    account: ProviderAccount,
    model_names: list[str],
    action: str,
) -> None:
    config = await get_alert_config(session)
    buffer = float(config["overspend_buffer_usd"])
    exhausted = credits_exhausted(account, buffer)
    status = desired_channel_status(account, exhausted)
    try:
        gateway = _o1_gateway()
    except NewApiError as exc:
        await _write_log(session, account, "newapi", str(exc), level="error")
        return

    host = (account.resource_name or "").strip().lower()
    base_url = openai_base_url(account.endpoint, account.resource_name)
    api_key = await decrypt_foundry_key(session, account.subscription_id, account.resource_name)
    if not api_key:
        await _write_log(
            session,
            account,
            "newapi",
            "Azure deploy succeeded, but there is no stored Foundry key so the gpt-astra channel was not added.",
            level="error",
        )
        return

    defaults = await get_deploy_defaults(session)
    async with _gateway_lock:
        try:
            await ensure_gpt_newapi_fixes(gateway)
        except (NewApiError, NewApiAuthError) as exc:
            await _write_log(session, account, "newapi", str(exc)[:400], level="error")
        channels = await fetch_channels(gateway)
        existing = match_gpt_channel(channels, host)
        models = merge_model_list((existing or {}).get("models"), model_names)
        try:
            if existing is None:
                name = await _create_gpt_channel(
                    gateway,
                    channels,
                    name_hint=next_gpt_channel_name(channels),
                    api_key=api_key,
                    base_url=base_url,
                    models=models,
                    priority=defaults["priority"],
                    weight=defaults["weight"],
                    status=status,
                    remark="",
                )
                if status != 1:
                    refreshed = await fetch_channels(gateway)
                    created = next((item for item in refreshed if item.get("name") == name), None)
                    if created and created.get("id") is not None and created.get("status") != status:
                        await set_channel_status(gateway, int(created["id"]), status)
                await _write_log(
                    session,
                    account,
                    "newapi",
                    _spend_message(account, name, models, status, created=True),
                )
                return
            changed = await _update_gpt_channel(
                gateway,
                existing,
                models=models,
                base_url=base_url,
                api_key=api_key,
                status=status,
                priority=defaults["priority"],
                weight=defaults["weight"],
            )
            name = str(existing.get("name") or "")
            if changed:
                await _write_log(session, account, "newapi", _spend_message(account, name, models, status, created=False))
            else:
                await _write_log(
                    session,
                    account,
                    "newapi",
                    f"{name} already lists {models} and spend stays on {account.name}.",
                )
        except (NewApiError, NewApiAuthError) as exc:
            await _write_log(session, account, "newapi", str(exc)[:400], level="error")


def _spend_message(account: ProviderAccount, channel_name: str, models: str, status: int, created: bool) -> str:
    verb = "Created" if created else "Updated"
    gate = "enabled" if status == 1 else "left disabled"
    same = account.new_api_name or account.name
    return (
        f"{verb} O1 channel {channel_name} (tag {GPT_CHANNEL_TAG}, group {GPT_CHANNEL_GROUP}) "
        f"with {models}. It uses the same Azure resource as {same}, so its NewAPI spend is added "
        f"under {account.name}. When that combined spend hits the stop limit, Kimi and GPT channels "
        f"on this resource are disabled together. Channel is {gate}."
    )


def _channel_fields(
    *,
    name: str,
    base_url: str,
    models: str,
    priority: int,
    weight: int,
    status: int,
    remark: str,
    api_key: str | None = None,
    channel_id: int | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": GPT_CHANNEL_TYPE,
        "name": name,
        "base_url": base_url,
        "other": GPT_AZURE_API_VERSION,
        "models": models,
        "group": GPT_CHANNEL_GROUP,
        "tag": GPT_CHANNEL_TAG,
        "model_mapping": "",
        "priority": priority,
        "weight": weight,
        "auto_ban": 1,
        "status": status,
        "setting": GPT_SETTING,
        "settings": GPT_SETTINGS,
        "param_override": GPT_PARAM_OVERRIDE,
        "openai_organization": "",
        "test_model": "",
        "status_code_mapping": "",
        "header_override": "",
        "remark": remark,
    }
    if channel_id is not None:
        body["id"] = channel_id
    if api_key:
        body["key"] = api_key
    return body


async def _create_gpt_channel(
    gateway: Gateway,
    channels: list[dict],
    *,
    name_hint: str,
    api_key: str,
    base_url: str,
    models: str,
    priority: int,
    weight: int,
    status: int,
    remark: str,
) -> str:
    name = name_hint
    last_error = "Could not allocate a gpt-astra channel name."
    for _ in range(8):
        taken = any(str(channel.get("name") or "") == name for channel in channels)
        if taken:
            match = GPT_CHANNEL_RE.match(name)
            number = int(match.group(1)) + 1 if match else 0
            name = f"gpt-astra-proxy{number}"
            continue
        body = {
            "mode": "single",
            "channel": _channel_fields(
                name=name,
                base_url=base_url,
                models=models,
                priority=priority,
                weight=weight,
                status=status,
                remark=remark,
                api_key=api_key,
            ),
        }
        try:
            await _post_channel(gateway, body)
            return name
        except NewApiError as exc:
            last_error = str(exc)
            lowered = last_error.lower()
            if any(token in lowered for token in ("exist", "duplicate", "unique", "已存在", "重复", "名称")):
                channels = await fetch_channels(gateway)
                name = next_gpt_channel_name(channels)
                continue
            raise
    raise NewApiError(last_error)


async def _update_gpt_channel(
    gateway: Gateway,
    channel: dict,
    *,
    models: str,
    base_url: str,
    api_key: str,
    status: int,
    priority: int,
    weight: int,
) -> bool:
    current_models = merge_model_list(channel.get("models"), [])
    same_models = current_models == models
    same_url = _host_key(channel.get("base_url")) == _host_key(base_url)
    current_status = channel.get("status")
    override_ok = "max_completion_tokens" in str(channel.get("param_override") or "")
    group_ok = str(channel.get("group") or "") == GPT_CHANNEL_GROUP
    remark_ok = not str(channel.get("remark") or "").strip()
    if same_models and same_url and current_status == status and override_ok and group_ok and remark_ok:
        return False
    body = _channel_fields(
        name=str(channel.get("name") or ""),
        base_url=base_url,
        models=models,
        priority=int(channel.get("priority") if channel.get("priority") is not None else priority),
        weight=int(channel.get("weight") if channel.get("weight") is not None else weight),
        status=status,
        remark="",
        api_key=api_key,
        channel_id=channel.get("id"),
    )
    # Status is changed through the dedicated status endpoint. Including it here
    # makes O1 reject the update.
    body.pop("status", None)
    await _put_channel(gateway, body)
    if current_status != status and channel.get("id") is not None:
        await set_channel_status(gateway, int(channel["id"]), status)
    return True


async def _post_channel(gateway: Gateway, body: dict) -> None:
    import httpx

    async with httpx.AsyncClient(timeout=30, proxy=gateway.proxy) as client:
        response = await client.post(f"{gateway.base_url}/api/channel/", headers=_headers(gateway), json=body)
    _raise_channel(response, "create")


async def _put_channel(gateway: Gateway, body: dict) -> None:
    import httpx

    async with httpx.AsyncClient(timeout=30, proxy=gateway.proxy) as client:
        response = await client.put(f"{gateway.base_url}/api/channel/", headers=_headers(gateway), json=body)
    _raise_channel(response, "update")


def _option_map(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _channel_needs_token_fix(channel: dict) -> bool:
    if not _is_gpt_pool_channel(channel):
        return False
    group_ok = str(channel.get("group") or "") == GPT_CHANNEL_GROUP
    remark_ok = not str(channel.get("remark") or "").strip()
    override_ok = "max_completion_tokens" in str(channel.get("param_override") or "")
    return not (group_ok and remark_ok and override_ok)


async def ensure_gpt_newapi_fixes(gateway: Gateway) -> None:
    """Fill the missing gpt-6-sol price and rewrite max_tokens on gpt-astra channels."""
    import httpx

    async with httpx.AsyncClient(timeout=40, proxy=gateway.proxy) as client:
        response = await client.get(f"{gateway.base_url}/api/option/", headers=_headers(gateway))
        try:
            payload = response.json()
        except ValueError as exc:
            raise NewApiError(f"O1 option list returned non-JSON ({response.status_code})") from exc
        if response.status_code != 200 or payload.get("success") is False:
            raise NewApiError(f"O1 option list failed: {(payload.get('message') or response.text)[:200]}")
        rows = payload.get("data") or []
        stored = {
            str(item.get("key")): item.get("value")
            for item in rows
            if isinstance(item, dict) and item.get("key")
        }
        patches = {**GPT_PRICE_PATCHES, **GPT_GROUP_PATCHES}
        for key, patch in patches.items():
            raw = stored.get(key)
            if raw is None or raw == "":
                current = {}
            elif not isinstance(raw, str):
                continue
            else:
                current = _option_map(raw)
                if not current and raw.strip() not in {"", "{}"}:
                    continue
            merged = merge_price_maps(current, patch)
            if merged == current:
                continue
            written = json.dumps(merged, separators=(",", ":"))
            put = await client.put(
                f"{gateway.base_url}/api/option/",
                headers=_headers(gateway),
                json={"key": key, "value": written},
            )
            try:
                body = put.json()
            except ValueError as exc:
                raise NewApiError(f"O1 option update returned non-JSON ({put.status_code})") from exc
            if put.status_code != 200 or body.get("success") is False:
                raise NewApiError(f"O1 option {key} update failed: {(body.get('message') or put.text)[:200]}")

    channels = await fetch_channels(gateway)
    for channel in channels:
        if not _channel_needs_token_fix(channel):
            continue
        body = _channel_fields(
            name=str(channel.get("name") or ""),
            base_url=str(channel.get("base_url") or ""),
            models=str(channel.get("models") or ""),
            priority=int(channel.get("priority") or 0),
            weight=int(channel.get("weight") or 1),
            status=int(channel.get("status") or 1),
            remark="",
            channel_id=channel.get("id"),
        )
        body.pop("status", None)
        body["group"] = GPT_CHANNEL_GROUP
        if channel.get("other"):
            body["other"] = channel.get("other")
        if channel.get("setting"):
            body["setting"] = channel.get("setting")
        if channel.get("settings"):
            body["settings"] = channel.get("settings")
        await _put_channel(gateway, body)


def _raise_channel(response, action: str) -> None:
    try:
        payload = response.json()
    except ValueError as exc:
        if is_newapi_auth_failure(response.status_code, text=response.text[:200]):
            raise NewApiAuthError("O1", response.status_code, response.text[:200]) from exc
        raise NewApiError(f"O1 channel {action} returned non-JSON ({response.status_code})") from exc
    if not isinstance(payload, dict):
        payload = {}
    if is_newapi_auth_failure(response.status_code, payload, response.text[:200]):
        raise NewApiAuthError("O1", response.status_code, str(payload.get("message") or response.text)[:200])
    if response.status_code != 200 or payload.get("success") is False:
        raise NewApiError(f"O1 channel {action} failed: {(payload.get('message') or response.text)[:240]}")
