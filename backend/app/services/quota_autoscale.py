from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import SessionLocal
from app.models.azure_service_principal import AzureServicePrincipal
from app.models.provider_account import ProviderAccount
from app.repositories.account_repository import AccountRepository
from app.runtime import AZURE_SYNC_CONCURRENCY
from app.schemas.kimi_deploy import KimiDeployResult
from app.services.deploy_defaults import DEFAULT_WEIGHT
from app.services.kimi_deploy_service import KimiDeployError, lookup_accounts_inventory, scale_accounts
from app.services.kimi_newapi import is_kimi_channel_name
from app.services.join_group import looks_like_managed_stack
from app.services.notification_log import KIND_TPM_UPGRADE, add_log, prune
from app.services.service_principal_store import email_or_none, list_service_principals

logger = logging.getLogger(__name__)

# New deploys start at weight 1. After Azure TPM/RPM unlocks, route more traffic.
UPGRADED_NEWAPI_WEIGHT = 4


@dataclass(frozen=True)
class AutoscaleCandidate:
    account: dict[str, str]
    portal: ProviderAccount
    principal: AzureServicePrincipal


def is_kimi_k3_account(account: ProviderAccount) -> bool:
    if is_kimi_channel_name(account.new_api_name):
        return True
    return looks_like_managed_stack(account.resource_name, account.resource_group)


def is_newapi_enabled(account: ProviderAccount) -> bool:
    parts = {part.strip() for part in (account.new_api_gateway or "").upper().split("+") if part.strip()}
    if "O1" not in parts:
        return False
    if account.new_api_status_o1 is not None:
        return account.new_api_status_o1 == 1
    return account.new_api_status == 1


def is_live_newapi_enabled(result: KimiDeployResult) -> bool:
    return result.new_api_status == 1


def _target_key(account: ProviderAccount) -> tuple[str, str]:
    return (
        (account.subscription_id or "").strip().lower(),
        (account.resource_name or "").strip().lower(),
    )


def _payload(portal: ProviderAccount, sp: AzureServicePrincipal) -> dict[str, str]:
    return {
        "name": portal.name or sp.name or portal.resource_name,
        "account_holder": (sp.account_holder or "").strip(),
        "AZURE_TENANT_ID": sp.tenant_id,
        "AZURE_CLIENT_ID": sp.client_id,
        "AZURE_SUBSCRIPTION_ID": sp.subscription_id,
        "account_name": portal.resource_name,
        "resource_group": portal.resource_group,
        "azure_openai_endpoint": portal.endpoint,
        "person_associated": (portal.owner_tag or sp.owner_tag or "").strip(),
        "subscription_name": (sp.subscription_name or "").strip(),
        "deployment_name": "FW-Kimi-K3",
    }


def _as_int(value: int | float | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _fmt(value: int | float | None) -> str:
    number = _as_int(value)
    if number is None:
        return "?"
    if number >= 1000 and number % 1000 == 0:
        return f"{number // 1000}k"
    return str(number)


def needs_upgraded_weight(current: int | None) -> bool:
    return current is None or current == DEFAULT_WEIGHT


def _detail(
    before: KimiDeployResult,
    after: KimiDeployResult,
    *,
    weight_before: int | None = None,
    weight_after: int | None = None,
) -> str:
    text = (
        f"TPM {_fmt(before.tpm)} → {_fmt(after.tpm)}"
        f" · RPM {_fmt(before.rpm)} → {_fmt(after.rpm)}"
    )
    if weight_before != weight_after and (weight_before is not None or weight_after is not None):
        text += f" · weight {_fmt(weight_before)} → {_fmt(weight_after)}"
    return text


def _changed(before: KimiDeployResult, after: KimiDeployResult) -> bool:
    if not after.ok:
        return False
    return (
        _as_int(before.tpm),
        _as_int(before.rpm),
        _as_int(before.capacity),
    ) != (
        _as_int(after.tpm),
        _as_int(after.rpm),
        _as_int(after.capacity),
    )


def _log_email(*values: str | None) -> str | None:
    for value in values:
        parsed = email_or_none(value)
        if parsed:
            return parsed
    for value in values:
        text = (value or "").strip()
        if text:
            return text
    return None


def select_autoscale_targets(
    portals: list[ProviderAccount],
    principals: list[AzureServicePrincipal],
) -> list[AutoscaleCandidate]:
    by_sub = {
        (row.subscription_id or "").strip().lower(): row
        for row in principals
        if row.elevated_access and (row.subscription_id or "").strip()
    }
    out: list[AutoscaleCandidate] = []
    seen: set[tuple[str, str]] = set()
    for portal in portals:
        if not is_kimi_k3_account(portal) or not is_newapi_enabled(portal):
            continue
        key = _target_key(portal)
        if not key[0] or not key[1] or key in seen:
            continue
        sp = by_sub.get(key[0])
        if sp is None:
            continue
        seen.add(key)
        out.append(AutoscaleCandidate(_payload(portal, sp), portal, sp))
    return out


def _current_weight(candidate: AutoscaleCandidate, result: KimiDeployResult) -> int | None:
    return _as_int(result.new_api_weight) if result.new_api_weight is not None else _as_int(candidate.portal.new_api_weight)


async def _raise_newapi_weights(
    session: AsyncSession,
    pending: list[tuple[AutoscaleCandidate, KimiDeployResult]],
    scaled: list[KimiDeployResult],
) -> dict[int, tuple[int | None, int | None, str | None]]:
    """After a real TPM/RPM raise, move NewAPI routing weight from 1 to 4."""
    from app.services.kimi_newapi import rename_kimi_newapi_channel

    changes: dict[int, tuple[int | None, int | None, str | None]] = {}
    for index, ((candidate, before), after) in enumerate(zip(pending, scaled, strict=True)):
        if not after.ok or not _changed(before, after):
            continue
        current = _current_weight(candidate, after)
        if not needs_upgraded_weight(current):
            continue
        portal = candidate.portal
        routed = await rename_kimi_newapi_channel(
            session,
            weight=UPGRADED_NEWAPI_WEIGHT,
            channel_id=after.new_api_channel_id or portal.new_api_channel_id,
            subscription_id=(after.subscription_id or portal.subscription_id or ""),
            resource_name=(after.account_name or portal.resource_name or ""),
            endpoint=after.azure_openai_endpoint or portal.endpoint,
            sync_sheet=False,
        )
        if routed.ok:
            after.new_api_weight = routed.new_api_weight or UPGRADED_NEWAPI_WEIGHT
            after.new_api_priority = routed.new_api_priority or after.new_api_priority
            after.new_api_name = routed.new_api_name or after.new_api_name
            after.new_api_channel_id = routed.new_api_channel_id or after.new_api_channel_id
            changes[index] = (current, after.new_api_weight, None)
        else:
            message = routed.new_api_error or "Could not set NewAPI weight to 4."
            after.new_api_error = message
            logger.warning(
                "NewAPI weight %s → %s failed for %s: %s",
                current,
                UPGRADED_NEWAPI_WEIGHT,
                after.account_name or portal.resource_name,
                message,
            )
            changes[index] = (current, current, message)
    return changes


async def _write_logs(
    session: AsyncSession,
    pending: list[tuple[AutoscaleCandidate, KimiDeployResult]],
    scaled: list[KimiDeployResult],
    weight_changes: dict[int, tuple[int | None, int | None, str | None]] | None = None,
) -> tuple[int, int]:
    upgraded = 0
    failed = 0
    weight_changes = weight_changes or {}
    for index, ((candidate, before), after) in enumerate(zip(pending, scaled, strict=True)):
        portal = candidate.portal
        email = _log_email(after.email, candidate.account.get("account_holder"), before.email, candidate.principal.account_holder)
        owner = after.owner_tag or portal.owner_tag or candidate.account.get("person_associated")
        resource = after.account_name or portal.resource_name
        sub = after.subscription_id or portal.subscription_id
        channel = after.new_api_name or portal.new_api_name
        name = after.name or portal.name
        weight_before, weight_after, weight_error = weight_changes.get(index, (None, None, None))
        if after.ok and _changed(before, after):
            await add_log(
                session,
                kind=KIND_TPM_UPGRADE,
                status="ok",
                email=email,
                owner_tag=owner,
                account_name=name,
                resource_name=resource,
                subscription_id=sub,
                new_api_name=channel,
                detail=_detail(before, after, weight_before=weight_before, weight_after=weight_after),
                error=weight_error,
            )
            upgraded += 1
        elif not after.ok:
            await add_log(
                session,
                kind=KIND_TPM_UPGRADE,
                status="error",
                email=email,
                owner_tag=owner,
                account_name=name,
                resource_name=resource,
                subscription_id=sub,
                new_api_name=channel,
                detail=_detail(before, after),
                error=after.error or "TPM upgrade failed.",
            )
            failed += 1
    await session.commit()
    await prune(session)
    await session.commit()
    return upgraded, failed


async def run_auto_quota_upgrades() -> dict[str, int]:
    empty = {"checked": 0, "upgraded": 0, "failed": 0}
    try:
        async with SessionLocal() as session:
            targets = select_autoscale_targets(
                await AccountRepository(session).list_all(),
                await list_service_principals(session),
            )
            if not targets:
                return empty
            inventories = await lookup_accounts_inventory(
                [item.account for item in targets],
                session,
                refresh=True,
            )
            pending = [
                (candidate, inventory)
                for candidate, inventory in zip(targets, inventories, strict=True)
                if inventory.ok and inventory.tpm_upgrade_available and is_live_newapi_enabled(inventory)
            ]
            if not pending:
                return {"checked": len(targets), "upgraded": 0, "failed": 0}
            scaled = await scale_accounts(
                [item[0].account for item in pending],
                jobs=min(AZURE_SYNC_CONCURRENCY, len(pending)),
                session=session,
            )
            weight_changes = await _raise_newapi_weights(session, pending, scaled)
            upgraded, failed = await _write_logs(session, pending, scaled, weight_changes)
            return {"checked": len(targets), "upgraded": upgraded, "failed": failed}
    except KimiDeployError as exc:
        logger.warning("Auto TPM upgrade skipped: %s", exc)
        return empty
    except Exception:
        logger.exception("Auto TPM upgrade pass failed")
        return empty
