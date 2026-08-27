from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import SecretBox, get_secret_box
from app.models.azure_service_principal import AzureServicePrincipal
from app.models.provider_account import ProviderAccount
from app.services.owner_tag import person_from_payload


async def persist_service_principals(
    session: AsyncSession,
    accounts: list[dict],
    box: SecretBox | None = None,
) -> None:
    """Encrypt and upsert service principal credentials. Never log plaintext secrets."""
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
        holder = str(account.get("account_holder") or account.get("email") or "").strip() or None
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
                )
            )
        else:
            stored.tenant_id = tenant_id
            stored.client_id = client_id
            stored.client_secret_encrypted = encrypted
            if name:
                stored.name = name
            if holder:
                stored.account_holder = holder
            if sub_name:
                stored.subscription_name = sub_name
            if owner_tag:
                stored.owner_tag = owner_tag
            # Empty incoming person keeps stored.owner_tag; never copy it onto portal siblings.

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
        if subscription_id and (needs_secret or needs_ids):
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
                if not row.get("account_holder") and stored.account_holder:
                    row["account_holder"] = stored.account_holder
                if not row.get("subscription_name") and stored.subscription_name:
                    row["subscription_name"] = stored.subscription_name
        hydrated.append(row)
    return hydrated


async def list_service_principals(session: AsyncSession) -> list[AzureServicePrincipal]:
    result = await session.execute(
        select(AzureServicePrincipal).order_by(AzureServicePrincipal.name, AzureServicePrincipal.subscription_id)
    )
    return list(result.scalars())
