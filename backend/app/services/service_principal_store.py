from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import SecretBox, get_secret_box
from app.models.azure_service_principal import AzureServicePrincipal
from app.models.provider_account import ProviderAccount
from app.models.sp_submit_request import SpSubmitRequest
from app.services.owner_tag import person_from_payload


def looks_like_email(value: str | None) -> bool:
    text = (value or "").strip()
    return "@" in text and " " not in text and len(text) < 256


def email_or_none(value: str | None) -> str | None:
    text = (value or "").strip()
    return text if looks_like_email(text) else None


async def persist_service_principals(
    session: AsyncSession,
    accounts: list[dict],
    box: SecretBox | None = None,
    *,
    elevated_access: bool = True,
) -> None:
    """Encrypt and upsert every Deploy K3 identity. Secrets stay here for rotate/undeploy/filter."""
    box = box or get_secret_box()
    wrote = False
    for account in accounts:
        subscription_id = str(account.get("AZURE_SUBSCRIPTION_ID") or account.get("subscription_id") or "").strip()
        tenant_id = str(account.get("AZURE_TENANT_ID") or "").strip()
        client_id = str(account.get("AZURE_CLIENT_ID") or "").strip()
        secret = str(account.get("AZURE_CLIENT_SECRET") or "").strip()
        if not subscription_id or not tenant_id or not client_id or not secret:
            continue
        encrypted = box.encrypt(secret)
        name = str(account.get("name") or "").strip() or None
        holder = email_or_none(str(account.get("account_holder") or account.get("email") or ""))
        sub_name = str(account.get("subscription_name") or "").strip() or None
        owner_tag = person_from_payload(account)

        existing = await session.execute(
            select(AzureServicePrincipal).where(AzureServicePrincipal.subscription_id == subscription_id)
        )
        stored = existing.scalar_one_or_none()
        if stored is None:
            session.add(
                AzureServicePrincipal(
                    subscription_id=subscription_id,
                    tenant_id=tenant_id,
                    client_id=client_id,
                    client_secret_encrypted=encrypted,
                    name=name,
                    account_holder=holder,
                    subscription_name=sub_name,
                    owner_tag=owner_tag,
                    elevated_access=True,
                )
            )
        else:
            stored.tenant_id = tenant_id
            stored.client_id = client_id
            stored.client_secret_encrypted = encrypted
            stored.elevated_access = True
            if name:
                stored.name = name
            if holder:
                stored.account_holder = holder
            if sub_name:
                stored.subscription_name = sub_name
            if owner_tag:
                stored.owner_tag = owner_tag

        portal = await session.execute(select(ProviderAccount).where(ProviderAccount.subscription_id == subscription_id))
        for row in portal.scalars():
            if row.client_id != client_id:
                continue
            row.tenant_id = tenant_id
            row.client_secret_encrypted = encrypted
        wrote = True
    if wrote:
        await session.commit()


async def hydrate_service_principals(
    session: AsyncSession,
    accounts: list[dict[str, str]],
    box: SecretBox | None = None,
) -> list[dict[str, str]]:
    """Fill missing SP fields from the stored row for that subscription. Secret never leaves this function except into the in-memory payload used for Azure calls."""
    box = box or get_secret_box()
    hydrated: list[dict[str, str]] = []
    for account in accounts:
        row = dict(account)
        subscription_id = (row.get("AZURE_SUBSCRIPTION_ID") or "").strip()
        needs_secret = not (row.get("AZURE_CLIENT_SECRET") or "").strip()
        needs_ids = not (row.get("AZURE_TENANT_ID") or "").strip() or not (row.get("AZURE_CLIENT_ID") or "").strip()
        if not email_or_none(row.get("account_holder")):
            row.pop("account_holder", None)
        if subscription_id and (needs_secret or needs_ids or not email_or_none(row.get("account_holder"))):
            stored = (
                await session.execute(
                    select(AzureServicePrincipal).where(AzureServicePrincipal.subscription_id == subscription_id)
                )
            ).scalar_one_or_none()
            if stored is None:
                portal_rows = list(
                    (
                        await session.execute(
                            select(ProviderAccount).where(ProviderAccount.subscription_id == subscription_id)
                        )
                    ).scalars()
                )
                wanted_client = (row.get("AZURE_CLIENT_ID") or "").strip()
                if wanted_client:
                    with_secret = [
                        item
                        for item in portal_rows
                        if item.client_id == wanted_client and item.client_secret_encrypted
                    ]
                    match = with_secret[0] if len(with_secret) == 1 else None
                else:
                    with_secret = [item for item in portal_rows if item.client_secret_encrypted]
                    match = with_secret[0] if len(with_secret) == 1 else None
                if match is not None:
                    if not (row.get("AZURE_TENANT_ID") or "").strip():
                        row["AZURE_TENANT_ID"] = match.tenant_id
                    if not (row.get("AZURE_CLIENT_ID") or "").strip():
                        row["AZURE_CLIENT_ID"] = match.client_id
                    if needs_secret:
                        row["AZURE_CLIENT_SECRET"] = box.decrypt(match.client_secret_encrypted)
                    if not row.get("name") and match.name:
                        row["name"] = match.name
            else:
                request_client = (row.get("AZURE_CLIENT_ID") or "").strip()
                if not (row.get("AZURE_TENANT_ID") or "").strip():
                    row["AZURE_TENANT_ID"] = stored.tenant_id
                if not request_client:
                    row["AZURE_CLIENT_ID"] = stored.client_id
                if needs_secret and (not request_client or request_client == stored.client_id):
                    row["AZURE_CLIENT_SECRET"] = box.decrypt(stored.client_secret_encrypted)
                if not row.get("name") and stored.name:
                    row["name"] = stored.name
                if not row.get("account_holder") and email_or_none(stored.account_holder):
                    row["account_holder"] = stored.account_holder
                if not row.get("subscription_name") and stored.subscription_name:
                    row["subscription_name"] = stored.subscription_name
        hydrated.append(row)
    return hydrated


def _is_dedicated_kimi_stack(resource_group: str | None, resource_name: str | None) -> bool:
    rg = (resource_group or "").strip().lower()
    rn = (resource_name or "").strip().lower()
    return (rg.startswith("rg-") and rg.endswith("-kimi")) or "-kimi-" in rn


async def sync_elevated_from_dedicated_kimi(session: AsyncSession) -> None:
    """Dedicated K3 stacks on the portal belong on Deploy K3, including deploys that skipped SP persist."""
    existing = {
        (sub or "").strip().lower()
        for sub in (await session.execute(select(AzureServicePrincipal.subscription_id))).scalars()
        if (sub or "").strip()
    }
    added = False
    portal_rows = list((await session.execute(select(ProviderAccount))).scalars())
    for account in portal_rows:
        sub = (account.subscription_id or "").strip()
        if not sub or sub.lower() in existing:
            continue
        if not account.client_secret_encrypted:
            continue
        if not _is_dedicated_kimi_stack(account.resource_group, account.resource_name):
            continue
        session.add(
            AzureServicePrincipal(
                subscription_id=sub,
                tenant_id=account.tenant_id,
                client_id=account.client_id,
                client_secret_encrypted=account.client_secret_encrypted,
                name=account.name,
                account_holder=None,
                owner_tag=account.owner_tag,
                elevated_access=True,
            )
        )
        existing.add(sub.lower())
        added = True
    if added:
        await session.commit()


async def apply_join_emails(session: AsyncSession) -> None:
    """Copy Microsoft login emails from approved joins onto stored Deploy K3 identities."""
    joins = list(
        (
            await session.execute(
                select(SpSubmitRequest).where(
                    SpSubmitRequest.status == "approved",
                    SpSubmitRequest.subscription_id.is_not(None),
                    SpSubmitRequest.account_holder.is_not(None),
                )
            )
        ).scalars()
    )
    by_sub = {
        (row.subscription_id or "").strip().lower(): email_or_none(row.account_holder)
        for row in joins
        if (row.subscription_id or "").strip()
    }
    changed = False
    stored_rows = list((await session.execute(select(AzureServicePrincipal))).scalars())
    for stored in stored_rows:
        if email_or_none(stored.account_holder):
            continue
        email = by_sub.get((stored.subscription_id or "").strip().lower())
        if not email:
            if stored.account_holder and not looks_like_email(stored.account_holder):
                stored.account_holder = None
                changed = True
            continue
        stored.account_holder = email
        changed = True
    if changed:
        await session.commit()


async def drop_stored_principal(session: AsyncSession, subscription_id: str | None) -> None:
    """Remove a Deploy K3 stored identity after undeploy, even if the portal account remains."""
    sub = (subscription_id or "").strip().lower()
    if not sub:
        return
    stored = (
        await session.execute(
            select(AzureServicePrincipal).where(func.lower(AzureServicePrincipal.subscription_id) == sub)
        )
    ).scalar_one_or_none()
    if stored is not None:
        await session.delete(stored)
        await session.commit()


async def drop_orphan_service_principal(session: AsyncSession, subscription_id: str | None) -> None:
    """Remove a stored SP when the subscription has no portal account, so /join can run again."""
    sub = (subscription_id or "").strip().lower()
    if not sub:
        return
    portal = (
        await session.execute(
            select(ProviderAccount.id).where(func.lower(ProviderAccount.subscription_id) == sub).limit(1)
        )
    ).scalar_one_or_none()
    if portal is not None:
        return
    stored = (
        await session.execute(
            select(AzureServicePrincipal).where(func.lower(AzureServicePrincipal.subscription_id) == sub)
        )
    ).scalar_one_or_none()
    if stored is not None:
        await session.delete(stored)


async def list_service_principals(session: AsyncSession) -> list[AzureServicePrincipal]:
    await apply_join_emails(session)
    result = await session.execute(
        select(AzureServicePrincipal).order_by(
            AzureServicePrincipal.created_at.desc(),
            AzureServicePrincipal.id.desc(),
        )
    )
    return list(result.scalars())
