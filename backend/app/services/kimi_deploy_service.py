from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import shutil
import subprocess
import tempfile
import time
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any, Awaitable, Callable

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AzureApiError
from app.providers.azure.arm_client import AzureArmClient
from app.providers.azure.token_provider import AzureTokenProvider
from app.providers.base import ProviderCredentials
from app.providers.registry import get_provider
from app.schemas.kimi_deploy import (
    KimiCreditSnapshot,
    KimiDeleteResult,
    KimiDeployResult,
    KimiDeployStatus,
    KimiSecretsRow,
    KimiTestResult,
)
from app.services.azure_inventory_cache import (
    credits_from_inventory,
    drop_azure_inventory,
    get_cached_azure_inventory,
    store_azure_inventory,
)
from app.services.owner_tag import apply_person_associated_tags, person_from_payload, resource_key

logger = logging.getLogger(__name__)

ProgressFn = Callable[[dict[str, Any]], Awaitable[None]]

REQUIRED_FIELDS = {
    "AZURE_SUBSCRIPTION_ID": ("AZURE_SUBSCRIPTION_ID", "subscription_id", "subscriptionId", "subscription"),
}
HYDRATABLE_FIELDS = {
    "AZURE_TENANT_ID": ("AZURE_TENANT_ID", "tenant_id", "tenantId", "tenant"),
    "AZURE_CLIENT_ID": ("AZURE_CLIENT_ID", "client_id", "clientId", "appId", "app_id"),
    "AZURE_CLIENT_SECRET": ("AZURE_CLIENT_SECRET", "client_secret", "clientSecret", "password"),
}

NAME_ALIASES = ("name", "account_name", "accountName", "ACCOUNT_NAME", "account")
HOLDER_ALIASES = ("account_holder", "accountHolder", "email", "ACCOUNT_HOLDER")


def _as_openai_endpoint(url: str | None) -> str | None:
    cleaned = (url or "").strip().rstrip("/")
    if not cleaned:
        return None
    cleaned = cleaned.replace(".cognitiveservices.azure.com", ".openai.azure.com")
    cleaned = cleaned.replace(".services.ai.azure.com", ".openai.azure.com")
    return f"{cleaned}/"


class KimiDeployError(ValueError):
    pass


def _norm_key(key: str) -> str:
    return key.replace("_", "").replace("-", "").lower()


def _as_record(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    return None


def _lookup(source: dict[str, Any]) -> dict[str, Any]:
    return {_norm_key(str(key)): value for key, value in source.items()}


def _pick(lookup: dict[str, Any], aliases: tuple[str, ...]) -> str:
    for alias in aliases:
        value = lookup.get(_norm_key(alias))
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _pick_int(lookup: dict[str, Any], aliases: tuple[str, ...]) -> int | None:
    for alias in aliases:
        value = lookup.get(_norm_key(alias))
        if isinstance(value, bool) or value is None or value == "":
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str) and value.strip():
            try:
                return int(value.strip())
            except ValueError:
                continue
    return None


def find_deploy_script() -> Path | None:
    here = Path(__file__).resolve()
    candidates = [
        here.parents[3] / "scripts" / "kimi_k3_deploy.py",
        Path("/app/scripts/kimi_k3_deploy.py"),
        here.parents[2] / "scripts" / "kimi_k3_deploy.py",
        Path.cwd() / "scripts" / "kimi_k3_deploy.py",
        Path.cwd().parent / "scripts" / "kimi_k3_deploy.py",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


@lru_cache(maxsize=1)
def load_deploy_module() -> ModuleType:
    path = find_deploy_script()
    if path is None:
        raise KimiDeployError("Deploy script scripts/kimi_k3_deploy.py was not found on the backend host.")
    spec = importlib.util.spec_from_file_location("kimi_k3_deploy", path)
    if spec is None or spec.loader is None:
        raise KimiDeployError(f"Could not load deploy script at {path}.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _host_account() -> dict[str, Any] | None:
    az = shutil.which("az")
    if not az:
        return None
    try:
        proc = subprocess.run(
            [az, "account", "show", "-o", "json"],
            capture_output=True,
            text=True,
            timeout=12,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    text = (proc.stdout or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def deploy_status() -> KimiDeployStatus:
    az_path = shutil.which("az")
    script = find_deploy_script()
    az_cli = bool(az_path)
    script_found = script is not None
    ready = az_cli and script_found
    if not az_cli:
        message = "Azure CLI (az) is not on the backend PATH. Install it on the host that runs the API."
    elif not script_found:
        message = "Deploy script scripts/kimi_k3_deploy.py was not found."
    else:
        message = "Ready to deploy FW-Kimi-K3 from pasted service-principal secrets."

    host = _host_account() if az_cli else None
    user = (host or {}).get("user") or {}
    user_type = user.get("type")
    az_user = user.get("name")
    can_bootstrap = bool(ready and user_type == "user")
    if not ready:
        bootstrap_message = message
    elif can_bootstrap:
        sub_name = (host or {}).get("name") or "this subscription"
        sub_id = (host or {}).get("id") or ""
        bootstrap_message = (
            f"Owner login {az_user}. Generate keys uses subscription {sub_name}"
            + (f" ({sub_id})" if sub_id else "")
            + "."
        )
    elif user_type:
        bootstrap_message = (
            f"Host az is logged in as {user_type} {az_user or ''}. Generate keys needs an Owner user login."
        )
    else:
        bootstrap_message = "Host az has no user login. Run `az login` as the subscription Owner to generate keys."

    return KimiDeployStatus(
        az_cli=az_cli,
        az_path=az_path,
        script_found=script_found,
        script_path=str(script) if script else None,
        ready=ready,
        message=message,
        can_bootstrap=can_bootstrap,
        az_user=az_user,
        az_user_type=user_type,
        subscription_id=(host or {}).get("id"),
        subscription_name=(host or {}).get("name"),
        bootstrap_message=bootstrap_message,
    )


def normalize_account(raw: dict[str, Any], index: int) -> dict[str, str]:
    sources = [raw]
    for nested_key in ("credentials", "azure", "sp", "servicePrincipal", "service_principal"):
        nested = _as_record(raw.get(nested_key))
        if nested:
            sources.append(nested)

    merged: dict[str, Any] = {}
    for source in reversed(sources):
        merged.update(_lookup(source))

    missing: list[str] = []
    out: dict[str, str] = {}
    for canonical, aliases in REQUIRED_FIELDS.items():
        value = _pick(merged, aliases)
        if not value:
            missing.append(canonical)
        else:
            out[canonical] = value
    if missing:
        raise KimiDeployError(f"Entry {index + 1} is missing {', '.join(missing)}.")
    for canonical, aliases in HYDRATABLE_FIELDS.items():
        value = _pick(merged, aliases)
        if value:
            out[canonical] = value

    name = _pick(merged, NAME_ALIASES)
    holder = _pick(merged, HOLDER_ALIASES)
    out["name"] = name or holder or f"account-{index + 1}"
    if holder:
        out["account_holder"] = holder
    rg = _pick(merged, ("resource_group", "resourceGroup", "AZURE_RESOURCE_GROUP"))
    foundry = _pick(merged, ("foundry_account", "account_name", "accountName", "azure_account_name"))
    endpoint = _pick(merged, ("azure_openai_endpoint", "endpoint", "AZURE_OPENAI_ENDPOINT"))
    deployment = _pick(merged, ("deployment_name", "deploymentName", "DEPLOYMENT_NAME"))
    if rg:
        out["resource_group"] = rg
    if foundry:
        out["account_name"] = foundry
    if endpoint:
        out["azure_openai_endpoint"] = endpoint.rstrip("/")
    if deployment:
        out["deployment_name"] = deployment
    sub_name = _pick(merged, ("subscription_name", "subscriptionName", "AZURE_SUBSCRIPTION_NAME"))
    if sub_name:
        out["subscription_name"] = sub_name
    try:
        person = person_from_payload(merged) or person_from_payload(raw)
    except ValueError as exc:
        raise KimiDeployError(str(exc)) from exc
    if person:
        out["person_associated"] = person
    new_api_name = _pick(merged, ("new_api_name", "newApiName", "channel_name", "channelName"))
    if new_api_name:
        out["new_api_name"] = new_api_name
    priority = _pick_int(merged, ("new_api_priority", "newApiPriority", "priority"))
    if priority is not None:
        if priority < 0 or priority > 10000:
            raise KimiDeployError(f"Entry {index + 1} priority must be 0–10000.")
        out["new_api_priority"] = str(priority)
    weight = _pick_int(merged, ("new_api_weight", "newApiWeight", "weight"))
    if weight is not None:
        if weight < 1 or weight > 10000:
            raise KimiDeployError(f"Entry {index + 1} weight must be 1–10000.")
        out["new_api_weight"] = str(weight)
    return out


def normalize_accounts(raw_accounts: list[dict[str, Any]]) -> list[dict[str, str]]:
    if not raw_accounts:
        raise KimiDeployError("Paste at least one account object.")
    return [normalize_account(item, index) for index, item in enumerate(raw_accounts)]


async def prepare_accounts(
    raw_accounts: list[dict[str, Any]],
    session: AsyncSession | None,
) -> list[dict[str, str]]:
    accounts = normalize_accounts(raw_accounts)
    if session is not None:
        from app.services.service_principal_store import hydrate_service_principals, persist_service_principals

        accounts = await hydrate_service_principals(session, accounts)
        await persist_service_principals(session, accounts)
        try:
            await apply_person_associated_tags(session, accounts)
        except ValueError as exc:
            raise KimiDeployError(str(exc)) from exc
    incomplete: list[str] = []
    for index, account in enumerate(accounts):
        missing = [
            field
            for field in ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET", "AZURE_SUBSCRIPTION_ID")
            if not (account.get(field) or "").strip()
        ]
        if missing:
            incomplete.append(f"Entry {index + 1} is missing {', '.join(missing)}.")
    if incomplete:
        raise KimiDeployError(
            " ".join(incomplete) + " Paste the secrets JSON once so the service principal can be stored."
        )
    return accounts


def _resource_identity(item: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for field in ("account_name", "resource_name", "azure_openai_endpoint", "endpoint"):
        value = item.get(field)
        if isinstance(value, str) and value.strip():
            key = resource_key(value)
            if key:
                keys.add(key)
    return keys


def _same_subscription(left: str | None, right: str | None) -> bool:
    a = (left or "").strip().lower()
    b = (right or "").strip().lower()
    return bool(a) and a == b


def _with_person(rows: list[dict[str, Any]], accounts: list[dict[str, str]]) -> list[dict[str, Any]]:
    tagged = [
        (_resource_identity(account), account["person_associated"])
        for account in accounts
        if account.get("person_associated")
    ]
    stamped: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        person = item.get("person_associated")
        row_keys = _resource_identity(item)
        if not person and row_keys:
            for acct_keys, tag in tagged:
                if acct_keys and row_keys & acct_keys:
                    person = tag
                    break
        if person:
            item["person_associated"] = person
        stamped.append(item)
    return stamped


def _safe_deploy(module: ModuleType, account: dict[str, str]) -> dict[str, Any]:
    name = account.get("name") or account.get("account_holder") or "account"
    try:
        result = module.deploy_one(account)
        if isinstance(result, dict):
            result.setdefault("ok", True)
            return result
        return {"ok": False, "name": name, "error": "Deploy returned an unexpected result."}
    except Exception as exc:  # noqa: BLE001 - surface Azure/CLI failures to the admin UI
        error = _scrub_secret(str(exc), account.get("AZURE_CLIENT_SECRET") or "")
        logger.warning("Kimi K3 deploy failed for %s: %s", name, error[-300:])
        return {"ok": False, "name": name, "error": error}


def _safe_delete(module: ModuleType, account: dict[str, str]) -> dict[str, Any]:
    name = account.get("name") or account.get("account_holder") or "account"
    try:
        result = module.delete_one(account)
        if isinstance(result, dict):
            result.setdefault("ok", True)
            return result
        return {"ok": False, "name": name, "error": "Delete returned an unexpected result.", "deleted": []}
    except Exception as exc:  # noqa: BLE001
        error = _scrub_secret(str(exc), account.get("AZURE_CLIENT_SECRET") or "")
        logger.warning("Kimi K3 undeploy failed for %s: %s", name, error[-300:])
        return {"ok": False, "name": name, "error": error, "deleted": []}


def _to_delete_result(raw: dict[str, Any]) -> KimiDeleteResult:
    deleted = raw.get("deleted") or []
    if not isinstance(deleted, list):
        deleted = [str(deleted)]
    return KimiDeleteResult(
        ok=bool(raw.get("ok")),
        name=raw.get("name"),
        account_name=raw.get("account_name"),
        resource_group=raw.get("resource_group"),
        subscription_id=raw.get("subscription_id"),
        subscription_name=raw.get("subscription_name"),
        deleted=[str(item) for item in deleted],
        message=raw.get("message"),
        error=raw.get("error"),
    )


def _safe_regenerate(module: ModuleType, account: dict[str, str]) -> dict[str, Any]:
    name = account.get("name") or account.get("account_holder") or "account"
    try:
        result = module.regenerate_one(account)
        if isinstance(result, dict):
            result.setdefault("ok", True)
            return result
        return {"ok": False, "name": name, "error": "Regenerate returned an unexpected result."}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Kimi K3 key regen failed for %s: %s", name, str(exc)[-300:])
        return {"ok": False, "name": name, "error": str(exc)[-1500:]}


def _credits_error_message(exc: BaseException | str) -> str:
    text = exc if isinstance(exc, str) else str(exc)
    lowered = text.lower()
    if "AADSTS7000215" in text or "invalid client secret" in lowered:
        return "Invalid client secret. Paste the latest secrets JSON, not an older file."
    if "AADSTS700016" in text or "application not found" in lowered:
        return "Azure AD does not recognize this application (client) ID."
    return text[-400:]


async def _fetch_account_credits(account: dict[str, str]) -> KimiCreditSnapshot:
    name = account.get("name") or account.get("account_holder") or "account"
    subscription_id = account.get("AZURE_SUBSCRIPTION_ID") or ""
    try:
        credentials = ProviderCredentials(
            tenant_id=account["AZURE_TENANT_ID"],
            client_id=account["AZURE_CLIENT_ID"],
            client_secret=account["AZURE_CLIENT_SECRET"],
            subscription_id=subscription_id,
        )
        provider = get_provider("azure_openai")
        balance = await provider.get_credit_balance(
            credentials,
            subscription_id,
            "",
            refresh=True,
        )
        if not balance.available:
            return KimiCreditSnapshot(
                ok=False,
                name=name,
                subscription_id=subscription_id,
                credits_currency=balance.currency or "USD",
                credits_label=balance.label,
                error="Azure did not return a credit grant for this subscription.",
            )
        return KimiCreditSnapshot(
            ok=True,
            name=name,
            subscription_id=subscription_id,
            credits_limit=balance.limit,
            credits_remaining=balance.remaining,
            credits_used=balance.used,
            credits_currency=balance.currency or "USD",
            credits_label=balance.label,
            credits_available=True,
        )
    except (AzureApiError, Exception) as exc:  # noqa: BLE001 - surface Azure failures to the admin UI
        logger.warning("Kimi credit lookup failed for %s: %s", name, str(exc)[-300:])
        detail = str(exc)
        response = getattr(exc, "response", None)
        if response is not None:
            try:
                detail = response.text or detail
            except Exception:  # noqa: BLE001
                pass
        return KimiCreditSnapshot(
            ok=False,
            name=name,
            subscription_id=subscription_id,
            error=_credits_error_message(detail),
        )


async def lookup_account_credits(account: dict[str, str]) -> KimiCreditSnapshot:
    name = account.get("name") or account.get("account_holder") or "account"
    subscription_id = account.get("AZURE_SUBSCRIPTION_ID") or ""
    resource_name = (account.get("account_name") or "").strip()
    if resource_name:
        cached = await get_cached_azure_inventory(subscription_id, resource_name)
        if cached is not None:
            snapshot = credits_from_inventory(cached, account)
            snapshot.name = name
            return snapshot
    return await _fetch_account_credits(account)


async def lookup_accounts_credits(
    raw_accounts: list[dict[str, Any]],
    session: AsyncSession | None = None,
) -> list[KimiCreditSnapshot]:
    accounts = await prepare_accounts(raw_accounts, session)
    return list(await asyncio.gather(*[lookup_account_credits(account) for account in accounts]))


def _is_kimi_deployment(name: str, model_name: str) -> bool:
    return name == "FW-Kimi-K3" or model_name == "FW-Kimi-K3"


async def _find_kimi_stack(account: dict[str, str]) -> KimiDeployResult:
    name = account.get("name") or account.get("account_holder") or "account"
    holder = account.get("account_holder") or ""
    subscription_id = account.get("AZURE_SUBSCRIPTION_ID") or ""
    try:
        credentials = ProviderCredentials(
            tenant_id=account["AZURE_TENANT_ID"],
            client_id=account["AZURE_CLIENT_ID"],
            client_secret=account["AZURE_CLIENT_SECRET"],
            subscription_id=subscription_id,
        )
        provider = get_provider("azure_openai")
        resources = await provider.discover_resources(credentials)

        async def deployments_for(resource: Any) -> tuple[Any, list[Any]]:
            try:
                return resource, await provider.list_deployments(credentials, resource.resource_id)
            except Exception:  # noqa: BLE001
                return resource, []

        listed = await asyncio.gather(*[deployments_for(resource) for resource in resources]) if resources else []
        matches: list[tuple[int, Any, Any]] = []
        for resource, deployments in listed:
            kimi = next(
                (item for item in deployments if _is_kimi_deployment(item.name, item.model_name)),
                None,
            )
            if kimi is None:
                continue
            score = 2 if "-kimi-" in (resource.name or "").lower() else 1
            matches.append((score, resource, kimi))
        if not matches:
            return KimiDeployResult(
                ok=False,
                name=name,
                email=holder,
                subscription_id=subscription_id,
                subscription_name=account.get("subscription_name") or None,
            )
        matches.sort(key=lambda item: -item[0])
        _score, resource, dep = matches[0]
        capacity = int(dep.capacity or 0)
        return KimiDeployResult(
            ok=True,
            name=name,
            email=holder,
            azure_openai_endpoint=_as_openai_endpoint(resource.endpoint),
            deployment_name=dep.name,
            model=dep.model_name,
            sku=dep.sku,
            capacity=capacity or None,
            tpm=capacity * 1000 if capacity else None,
            rpm=capacity or None,
            region=resource.location or None,
            account_name=resource.name,
            resource_group=resource.resource_group,
            subscription_id=subscription_id,
            subscription_name=account.get("subscription_name") or None,
        )
    except (AzureApiError, Exception) as exc:  # noqa: BLE001
        logger.warning("Kimi inventory lookup failed for %s: %s", name, str(exc)[-300:])
        detail = str(exc)
        response = getattr(exc, "response", None)
        if response is not None:
            try:
                detail = response.text or detail
            except Exception:  # noqa: BLE001
                pass
        return KimiDeployResult(
            ok=False,
            name=name,
            email=holder,
            subscription_id=subscription_id,
            subscription_name=account.get("subscription_name") or None,
            error=_credits_error_message(detail),
        )


async def _lookup_subscription_name(account: dict[str, str]) -> str:
    fallback = account.get("subscription_name") or ""
    subscription_id = account.get("AZURE_SUBSCRIPTION_ID") or ""
    if not subscription_id:
        return fallback
    try:
        credentials = ProviderCredentials(
            tenant_id=account["AZURE_TENANT_ID"],
            client_id=account["AZURE_CLIENT_ID"],
            client_secret=account["AZURE_CLIENT_SECRET"],
            subscription_id=subscription_id,
        )
        detail = await AzureArmClient(AzureTokenProvider()).get(
            credentials,
            f"/subscriptions/{subscription_id}",
            params={"api-version": "2022-12-01"},
        )
        return str(detail.get("displayName") or detail.get("name") or fallback).strip()
    except Exception:  # noqa: BLE001
        logger.info("Subscription name lookup failed for %s", subscription_id[-12:], exc_info=True)
        return fallback


def _with_account_meta(result: KimiDeployResult, account: dict[str, str]) -> KimiDeployResult:
    result.name = account.get("name") or result.name
    result.email = account.get("account_holder") or result.email
    if account.get("subscription_name") and not result.subscription_name:
        result.subscription_name = account["subscription_name"]
    if account.get("person_associated"):
        result.owner_tag = account["person_associated"]
    result.new_api_present = False
    result.new_api_created = False
    result.new_api_channel_id = None
    result.new_api_name = None
    result.new_api_status = None
    result.new_api_status_label = None
    result.new_api_priority = None
    result.new_api_weight = None
    result.new_api_error = None
    return result


async def lookup_account_inventory(account: dict[str, str]) -> KimiDeployResult:
    cached = await get_cached_azure_inventory(
        account.get("AZURE_SUBSCRIPTION_ID"),
        account.get("account_name"),
    )
    if cached is not None:
        return _with_account_meta(cached, account)
    stack, credits, subscription_name = await asyncio.gather(
        _find_kimi_stack(account),
        _fetch_account_credits(account),
        _lookup_subscription_name(account),
    )
    result = _merge_credits(stack, credits)
    if subscription_name:
        result.subscription_name = subscription_name
    elif not result.subscription_name:
        result.subscription_name = credits.subscription_name
    result = _with_account_meta(result, account)
    await store_azure_inventory(result)
    return result


async def lookup_accounts_inventory(
    raw_accounts: list[dict[str, Any]],
    session: AsyncSession | None = None,
    refresh: bool = False,
) -> list[KimiDeployResult]:
    accounts = await prepare_accounts(raw_accounts, session)
    if refresh:
        from app.services.azure_inventory_cache import drop_azure_inventory
        from app.services.kimi_newapi import invalidate_kimi_pool_cache

        invalidate_kimi_pool_cache()
        for account in accounts:
            await drop_azure_inventory(account.get("AZURE_SUBSCRIPTION_ID"), account.get("account_name"))
    results = list(await asyncio.gather(*[lookup_account_inventory(account) for account in accounts]))
    if session is not None:
        from app.repositories.account_repository import AccountRepository
        from app.services.kimi_newapi import attach_kimi_newapi_status

        await attach_kimi_newapi_status(session, results, accounts)
        repo = AccountRepository(session)
        portal_rows = await repo.list_all()
        for result, account in zip(results, accounts, strict=True):
            if result.owner_tag:
                continue
            if account.get("person_associated"):
                result.owner_tag = account["person_associated"]
                continue
            sub = (result.subscription_id or account.get("AZURE_SUBSCRIPTION_ID") or "").strip()
            resource = (result.account_name or account.get("account_name") or "").strip()
            if not sub or not resource:
                continue
            wanted_res = resource.lower()
            for portal in portal_rows:
                if not _same_subscription(portal.subscription_id, sub):
                    continue
                if (portal.resource_name or "").strip().lower() != wanted_res:
                    continue
                if portal.owner_tag:
                    result.owner_tag = portal.owner_tag
                break
    return results


def _merge_credits(result: KimiDeployResult, credits: KimiCreditSnapshot) -> KimiDeployResult:
    result.credits_limit = credits.credits_limit
    result.credits_remaining = credits.credits_remaining
    result.credits_used = credits.credits_used
    result.credits_currency = credits.credits_currency
    result.credits_label = credits.credits_label
    result.credits_available = credits.credits_available
    if not result.subscription_id:
        result.subscription_id = credits.subscription_id
    return result


def _to_result(raw: dict[str, Any]) -> KimiDeployResult:
    return KimiDeployResult(
        ok=bool(raw.get("ok")),
        name=raw.get("name"),
        email=raw.get("email"),
        azure_openai_endpoint=_as_openai_endpoint(raw.get("azure_openai_endpoint")),
        deployment_name=raw.get("deployment_name"),
        model=raw.get("model"),
        sku=raw.get("sku"),
        tpm=raw.get("tpm"),
        rpm=raw.get("rpm"),
        capacity=raw.get("capacity"),
        quota_limit=raw.get("quota_limit"),
        region=raw.get("region"),
        account_name=raw.get("account_name"),
        resource_group=raw.get("resource_group"),
        subscription_id=raw.get("subscription_id"),
        subscription_name=raw.get("subscription_name"),
        credits_limit=raw.get("credits_limit"),
        credits_remaining=raw.get("credits_remaining"),
        credits_used=raw.get("credits_used"),
        credits_currency=raw.get("credits_currency"),
        credits_label=raw.get("credits_label"),
        credits_available=bool(raw.get("credits_available")),
        error=raw.get("error"),
        owner_tag=raw.get("owner_tag") or raw.get("person_associated"),
    )


def _to_secrets_row(raw: dict[str, Any]) -> KimiSecretsRow:
    return KimiSecretsRow(
        ok=bool(raw.get("ok")),
        name=raw.get("name"),
        account_holder=raw.get("account_holder") or raw.get("email"),
        AZURE_TENANT_ID=raw.get("AZURE_TENANT_ID"),
        AZURE_CLIENT_ID=raw.get("AZURE_CLIENT_ID"),
        AZURE_CLIENT_SECRET=None,
        AZURE_SUBSCRIPTION_ID=raw.get("AZURE_SUBSCRIPTION_ID"),
        subscription_name=raw.get("subscription_name"),
        error=raw.get("error"),
    )


def _secrets_public(record: dict[str, Any]) -> dict[str, Any]:
    module = load_deploy_module()
    if hasattr(module, "secrets_row"):
        return module.secrets_row(record)
    return {
        "name": record.get("name"),
        "account_holder": record.get("account_holder") or "",
        "AZURE_TENANT_ID": record.get("AZURE_TENANT_ID"),
        "AZURE_CLIENT_ID": record.get("AZURE_CLIENT_ID"),
        "AZURE_CLIENT_SECRET": record.get("AZURE_CLIENT_SECRET"),
        "AZURE_SUBSCRIPTION_ID": record.get("AZURE_SUBSCRIPTION_ID"),
        "subscription_name": record.get("subscription_name") or "",
    }


async def deploy_accounts(
    raw_accounts: list[dict[str, Any]],
    jobs: int,
    session: AsyncSession | None = None,
    new_api_priority: int = 13,
    new_api_weight: int = 1,
    on_progress: ProgressFn | None = None,
) -> list[KimiDeployResult]:
    status = deploy_status()
    if not status.ready:
        raise KimiDeployError(status.message)

    accounts = await prepare_accounts(raw_accounts, session)
    module = load_deploy_module()
    workers = min(max(1, jobs), len(accounts))
    logger.info("Kimi K3 deploy started for %s account(s), jobs=%s", len(accounts), workers)
    if on_progress is not None:
        await on_progress({"type": "start", "total": len(accounts), "phase": "azure", "message": "Deploying Azure stacks in parallel"})

    sem = asyncio.Semaphore(workers)
    finished = 0
    progress_lock = asyncio.Lock()
    persist_lock = asyncio.Lock()
    portal_service = None
    persist_foundry_api_keys = None
    if session is not None:
        from app.core.crypto import get_secret_box
        from app.repositories.account_repository import AccountRepository
        from app.services.account_service import AccountService
        from app.services.openai_key_store import persist_foundry_api_keys as _persist_keys

        persist_foundry_api_keys = _persist_keys
        portal_service = AccountService(AccountRepository(session), get_secret_box())

    async def persist_one(account: dict[str, str], raw: dict[str, Any], result: KimiDeployResult) -> None:
        if session is None or not result.ok:
            return
        async with persist_lock:
            row = dict(raw)
            if account.get("person_associated"):
                row["person_associated"] = account["person_associated"]
            if persist_foundry_api_keys is not None:
                try:
                    await persist_foundry_api_keys(session, [row])
                except Exception:
                    logger.exception("Could not store Foundry API keys after deploy")
            if portal_service is None or not result.account_name:
                return
            try:
                await portal_service.upsert_from_kimi_deploy(
                    payload=account,
                    resource_name=result.account_name,
                    resource_group=result.resource_group or "",
                    endpoint=result.azure_openai_endpoint or "",
                    location=result.region or "",
                    owner_tag=result.owner_tag,
                    credits_limit=result.credits_limit,
                    credits_remaining=result.credits_remaining,
                    credits_used=result.credits_used,
                    credits_currency=result.credits_currency,
                    credits_label=result.credits_label,
                    deployment_name=result.deployment_name,
                )
            except Exception:
                logger.exception("Could not create portal account after Kimi deploy for %s", result.account_name)

    async def run_one(index: int, account: dict[str, str]) -> dict[str, Any]:
        nonlocal finished
        async with sem:
            raw = await asyncio.to_thread(_safe_deploy, module, account)
        result = _to_result(raw)
        if account.get("person_associated"):
            result.owner_tag = account["person_associated"]
        result.name = result.name or account.get("name")
        result.email = result.email or account.get("account_holder")
        await persist_one(account, raw, result)
        async with progress_lock:
            finished += 1
            done = finished
        if on_progress is not None:
            await on_progress(
                {
                    "type": "account",
                    "index": index,
                    "done": done,
                    "total": len(accounts),
                    "phase": "azure",
                    "result": result.model_dump(mode="json"),
                }
            )
        return raw

    raw_results, credit_results = await asyncio.gather(
        asyncio.gather(*[run_one(index, account) for index, account in enumerate(accounts)]),
        asyncio.gather(*[_fetch_account_credits(account) for account in accounts]),
    )
    results = [_merge_credits(_to_result(item), credits) for item, credits in zip(raw_results, credit_results, strict=True)]
    for result, account in zip(results, accounts, strict=True):
        if account.get("person_associated"):
            result.owner_tag = account["person_associated"]
        result.name = result.name or account.get("name")
        result.email = result.email or account.get("account_holder")
        await store_azure_inventory(result)
        if portal_service is None or not result.ok or not result.account_name or not result.credits_available:
            continue
        try:
            await portal_service.upsert_from_kimi_deploy(
                payload=account,
                resource_name=result.account_name,
                resource_group=result.resource_group or "",
                endpoint=result.azure_openai_endpoint or "",
                location=result.region or "",
                owner_tag=result.owner_tag,
                credits_limit=result.credits_limit,
                credits_remaining=result.credits_remaining,
                credits_used=result.credits_used,
                credits_currency=result.credits_currency,
                credits_label=result.credits_label,
                deployment_name=result.deployment_name,
            )
        except Exception:
            logger.exception("Could not update portal credits after Kimi deploy for %s", result.account_name)
    if session is not None:
        from app.services.kimi_newapi import ensure_kimi_newapi_channels

        if on_progress is not None:
            await on_progress({"type": "phase", "phase": "newapi", "message": "Adding O1 NewAPI channels", "total": len(accounts)})
        try:
            await ensure_kimi_newapi_channels(
                session,
                results,
                accounts,
                priority=new_api_priority,
                weight=new_api_weight,
                only_ok=True,
            )
        except Exception:
            logger.exception("Could not add NewAPI channels after Kimi deploy")
            for result in results:
                if result.ok and not result.new_api_present and not result.new_api_error:
                    result.new_api_error = "Could not add NewAPI channel."
    from app.services.google_sheet_inventory import sync_deploy_results

    await sync_deploy_results(results)
    return results


async def add_kimi_newapi_channels(
    raw_accounts: list[dict[str, Any]],
    session: AsyncSession,
    *,
    priority: int = 13,
    weight: int = 1,
) -> list[KimiDeployResult]:
    accounts = await prepare_accounts(raw_accounts, session)
    results = list(await asyncio.gather(*[lookup_account_inventory(account) for account in accounts]))
    for result, account in zip(results, accounts, strict=True):
        if not result.account_name and account.get("account_name"):
            result.account_name = account["account_name"]
        if not result.azure_openai_endpoint and account.get("azure_openai_endpoint"):
            result.azure_openai_endpoint = account["azure_openai_endpoint"]
        if not result.resource_group and account.get("resource_group"):
            result.resource_group = account["resource_group"]
        if account.get("person_associated"):
            result.owner_tag = account["person_associated"]
    from app.services.kimi_newapi import ensure_kimi_newapi_channels

    await ensure_kimi_newapi_channels(
        session,
        results,
        accounts,
        priority=priority,
        weight=weight,
        only_ok=False,
    )
    from app.services.google_sheet_inventory import sync_deploy_results

    await sync_deploy_results(results)
    return results


async def regenerate_accounts(
    raw_accounts: list[dict[str, Any]],
    jobs: int,
    session: AsyncSession | None = None,
) -> list[KimiSecretsRow]:
    status = deploy_status()
    if not status.ready:
        raise KimiDeployError(status.message)

    accounts = await prepare_accounts(raw_accounts, session)
    module = load_deploy_module()
    workers = min(max(1, jobs), len(accounts))
    logger.info("Kimi K3 key regen started for %s account(s), jobs=%s", len(accounts), workers)

    sem = asyncio.Semaphore(workers)

    async def run_one(account: dict[str, str]) -> dict[str, Any]:
        async with sem:
            return await asyncio.to_thread(_safe_regenerate, module, account)

    raw_results = await asyncio.gather(*[run_one(account) for account in accounts])
    if session is not None:
        from app.services.service_principal_store import persist_service_principals

        rotated: list[dict[str, str]] = []
        for item, account in zip(raw_results, accounts, strict=True):
            secret = item.get("AZURE_CLIENT_SECRET") if isinstance(item, dict) else None
            if item.get("ok") and secret:
                row = dict(account)
                row["AZURE_CLIENT_SECRET"] = str(secret)
                rotated.append(row)
        if rotated:
            try:
                await persist_service_principals(session, rotated)
            except Exception:
                logger.exception("Could not store rotated service principal secrets")
    for account in accounts:
        await drop_azure_inventory(account.get("AZURE_SUBSCRIPTION_ID"), account.get("account_name"))
    return [_to_secrets_row(item) for item in raw_results]


async def delete_accounts(
    raw_accounts: list[dict[str, Any]],
    jobs: int,
    session: AsyncSession | None = None,
) -> list[KimiDeleteResult]:
    status = deploy_status()
    if not status.ready:
        raise KimiDeployError(status.message)

    accounts = await prepare_accounts(raw_accounts, session)
    module = load_deploy_module()
    workers = min(max(1, jobs), len(accounts))
    logger.info("Kimi K3 undeploy started for %s account(s), jobs=%s", len(accounts), workers)

    sem = asyncio.Semaphore(workers)

    async def run_one(account: dict[str, str]) -> dict[str, Any]:
        async with sem:
            return await asyncio.to_thread(_safe_delete, module, account)

    raw_results = await asyncio.gather(*[run_one(account) for account in accounts])
    results = [_to_delete_result(item) for item in raw_results]
    for account, result in zip(accounts, results, strict=True):
        sub = account.get("AZURE_SUBSCRIPTION_ID") or result.subscription_id
        await drop_azure_inventory(sub, account.get("account_name"))
        if result.account_name:
            await drop_azure_inventory(result.subscription_id or sub, result.account_name)
    return results


def _run_bootstrap(name: str, email: str) -> dict[str, Any]:
    module = load_deploy_module()
    with tempfile.NamedTemporaryFile(prefix=f"kimi-{name}-", suffix=".json", delete=False) as handle:
        path = Path(handle.name)
    try:
        record = module.bootstrap(name, email, path)
        return _secrets_public(record)
    finally:
        path.unlink(missing_ok=True)


async def bootstrap_account(name: str, email: str) -> KimiSecretsRow:
    status = deploy_status()
    if not status.can_bootstrap:
        raise KimiDeployError(status.bootstrap_message or "Owner user `az login` is required to generate keys.")
    slug = "".join(ch if ch.isalnum() or ch in "-_" else "" for ch in name.strip())[:40]
    if not slug:
        raise KimiDeployError("Name must include letters or numbers.")
    logger.info("Kimi K3 bootstrap started for %s", slug)
    record = await asyncio.to_thread(_run_bootstrap, slug, email.strip())
    record["ok"] = True
    return _to_secrets_row(record)


_KEYS_API = "2023-05-01"
_TEST_PROMPT = "Reply with exactly the single word pong."
_TEST_MESSAGES = [{"role": "user", "content": _TEST_PROMPT}]
_COGNITIVE_SCOPE = "https://cognitiveservices.azure.com/.default"


def _scrub_secret(text: str, *secrets: str) -> str:
    out = text
    for secret in secrets:
        if secret and len(secret) > 6 and secret in out:
            out = out.replace(secret, "***")
    return out[-500:]


def _extract_reply(body: dict[str, Any]) -> str:
    if not isinstance(body, dict):
        return ""
    choices = body.get("choices") or []
    if choices:
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()[:280]
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if isinstance(part, str):
                    parts.append(part)
                elif isinstance(part, dict) and part.get("text"):
                    parts.append(str(part["text"]))
            joined = "".join(parts).strip()
            if joined:
                return joined[:280]
        for key in ("reasoning_content", "refusal"):
            value = message.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:280]
        text = choices[0].get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()[:280]
    output = body.get("output_text")
    if isinstance(output, str) and output.strip():
        return output.strip()[:280]
    return ""


def _unique_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        cleaned = (url or "").rstrip("/")
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            out.append(cleaned)
    return out


async def _data_plane_token(credentials: ProviderCredentials) -> str:
    try:
        return await AzureTokenProvider().get_token(credentials, scope=_COGNITIVE_SCOPE)
    except Exception:  # noqa: BLE001
        logger.info("Cognitive Services data-plane token failed", exc_info=True)
        return ""


def _auth_headers(api_key: str, aad_token: str) -> list[dict[str, str]]:
    headers: list[dict[str, str]] = []
    if api_key:
        headers.append({"api-key": api_key, "Content-Type": "application/json"})
    if aad_token:
        headers.append({"Authorization": f"Bearer {aad_token}", "Content-Type": "application/json"})
    return headers


def _chat_urls(base: str, deployment: str) -> list[str]:
    return [
        f"{base}/openai/deployments/{deployment}/chat/completions?api-version=2024-10-21",
        f"{base}/models/chat/completions?api-version=2024-05-01-preview",
    ]


def _chat_bodies(deployment: str) -> list[dict[str, Any]]:
    return [
        {"messages": _TEST_MESSAGES, "max_tokens": 32, "temperature": 0},
        {"model": deployment, "messages": _TEST_MESSAGES, "max_tokens": 32, "temperature": 0},
        {"messages": _TEST_MESSAGES, "max_completion_tokens": 32, "temperature": 0},
    ]


async def _post_chat(url: str, headers: dict[str, str], body: dict[str, Any]) -> tuple[int, dict[str, Any], str, int]:
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=15.0)) as client:
        response = await client.post(url, headers=headers, json=body)
    latency_ms = int((time.perf_counter() - started) * 1000)
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    text = response.text or ""
    return response.status_code, payload, text, latency_ms


async def test_account_model(
    account: dict[str, str],
    collected_keys: list[dict[str, Any]] | None = None,
) -> KimiTestResult:
    name = account.get("name") or account.get("account_holder") or "account"
    client_secret = account.get("AZURE_CLIENT_SECRET") or ""
    account_name = account.get("account_name") or ""
    resource_group = account.get("resource_group") or ""
    endpoint = (account.get("azure_openai_endpoint") or "").rstrip("/")
    deployment = account.get("deployment_name") or "FW-Kimi-K3"
    subscription_id = account.get("AZURE_SUBSCRIPTION_ID") or ""

    if not account_name or not resource_group or not endpoint:
        stack = await _find_kimi_stack(account)
        if not stack.ok:
            return KimiTestResult(
                ok=False,
                name=name,
                error=stack.error or "No FW-Kimi-K3 deployed on this subscription.",
            )
        account_name = stack.account_name or account_name
        resource_group = stack.resource_group or resource_group
        endpoint = (stack.azure_openai_endpoint or endpoint or "").rstrip("/")
        deployment = stack.deployment_name or deployment
        subscription_id = stack.subscription_id or subscription_id

    if not account_name or not resource_group or not subscription_id:
        return KimiTestResult(ok=False, name=name, error="Missing Foundry account or resource group to test.")

    credentials = ProviderCredentials(
        tenant_id=account["AZURE_TENANT_ID"],
        client_id=account["AZURE_CLIENT_ID"],
        client_secret=account["AZURE_CLIENT_SECRET"],
        subscription_id=subscription_id,
    )
    arm = AzureArmClient(AzureTokenProvider())
    resource_id = (
        f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
        f"/providers/Microsoft.CognitiveServices/accounts/{account_name}"
    )

    api_key = ""
    extra_endpoints: list[str] = []
    try:
        keys = await arm.post(credentials, f"{resource_id}/listKeys", json={}, params={"api-version": _KEYS_API})
        api_key = str(keys.get("key1") or keys.get("Key1") or keys.get("key2") or keys.get("Key2") or "")
    except AzureApiError as exc:
        logger.info("listKeys failed for %s: %s", account_name, str(exc)[-200:])
    try:
        detail = await arm.get(credentials, resource_id, params={"api-version": _KEYS_API})
        props = detail.get("properties") or {}
        if not endpoint:
            endpoint = str(props.get("endpoint") or "").rstrip("/")
        endpoints = props.get("endpoints") or {}
        if isinstance(endpoints, dict):
            for key in ("OpenAI Language Model Instance API", "Azure AI Model Inference API", "AI Foundry API"):
                value = endpoints.get(key)
                if isinstance(value, str) and value.strip():
                    extra_endpoints.append(value.rstrip("/"))
    except AzureApiError:
        pass

    bases = _unique_urls(
        [
            _as_openai_endpoint(endpoint) or "",
            *[ _as_openai_endpoint(item) or item for item in extra_endpoints ],
            endpoint,
            *extra_endpoints,
        ]
    )
    if collected_keys is not None and api_key and subscription_id and account_name:
        collected_keys.append(
            {
                "api_key": api_key,
                "subscription_id": subscription_id,
                "resource_name": account_name,
                "resource_group": resource_group,
                "endpoint": _as_openai_endpoint(endpoint) or (bases[0] if bases else endpoint),
                "deployment_name": deployment,
            }
        )
    if not bases:
        return KimiTestResult(
            ok=False,
            name=name,
            account_name=account_name,
            deployment_name=deployment,
            error="Could not resolve the model endpoint.",
        )

    aad_token = await _data_plane_token(credentials)
    headers_list = _auth_headers(api_key, aad_token)
    if not headers_list:
        return KimiTestResult(
            ok=False,
            name=name,
            account_name=account_name,
            deployment_name=deployment,
            endpoint=bases[0],
            error="Could not get an API key or data-plane token to call the model.",
        )

    last_error = "Model did not respond."
    for base in bases:
        unreachable = False
        for url in _chat_urls(base, deployment):
            not_found = False
            for headers in headers_list:
                for body in _chat_bodies(deployment):
                    try:
                        status, payload, text, latency_ms = await _post_chat(url, headers, body)
                    except httpx.RequestError as exc:
                        last_error = f"Could not reach the model endpoint ({exc.__class__.__name__})."
                        unreachable = True
                        break
                    if status == 200:
                        reply = _extract_reply(payload) or "(empty reply)"
                        return KimiTestResult(
                            ok=True,
                            name=name,
                            account_name=account_name,
                            deployment_name=deployment,
                            endpoint=base,
                            latency_ms=latency_ms,
                            reply=reply,
                        )
                    message = ""
                    if isinstance(payload.get("error"), dict):
                        message = str(payload["error"].get("message") or "")
                    message = message or text[:400] or f"HTTP {status}"
                    last_error = _scrub_secret(f"HTTP {status}: {message}", api_key, client_secret, aad_token)
                    if status in {401, 403}:
                        break
                    if status == 404:
                        not_found = True
                        break
                    if status == 429:
                        return KimiTestResult(
                            ok=False,
                            name=name,
                            account_name=account_name,
                            deployment_name=deployment,
                            endpoint=base,
                            error=last_error,
                        )
                if unreachable or not_found:
                    break
            if unreachable or not_found:
                break
        if unreachable:
            continue
    return KimiTestResult(
        ok=False,
        name=name,
        account_name=account_name,
        deployment_name=deployment,
        endpoint=bases[0],
        error=last_error,
    )


async def test_accounts(
    raw_accounts: list[dict[str, Any]],
    session: AsyncSession | None = None,
) -> list[KimiTestResult]:
    accounts = await prepare_accounts(raw_accounts, session)
    collected_keys: list[dict[str, Any]] = []
    results = list(await asyncio.gather(*[test_account_model(account, collected_keys) for account in accounts]))
    if session is not None and collected_keys:
        from app.services.openai_key_store import persist_foundry_api_keys

        try:
            await persist_foundry_api_keys(session, _with_person(collected_keys, accounts))
        except Exception:
            logger.exception("Could not store Foundry API keys after model test")
    return results
