from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import SecretBox, get_secret_box
from app.models.azure_openai_key import AzureOpenaiKey
from app.models.provider_account import ProviderAccount
from app.services.owner_tag import person_from_payload, resource_key


async def persist_foundry_api_keys(
    session: AsyncSession,
    rows: list[dict],
    box: SecretBox | None = None,
) -> None:
    """Encrypt and store Foundry data-plane keys. Never log or return plaintext."""
    box = box or get_secret_box()
    wrote = False
    for row in rows:
        api_key = str(row.get("api_key") or "").strip()
        subscription_id = str(row.get("subscription_id") or "").strip()
        resource_name = str(row.get("resource_name") or row.get("account_name") or "").strip()
        if not api_key or not subscription_id or not resource_name:
            continue
        encrypted = box.encrypt(api_key)
        endpoint = str(row.get("endpoint") or row.get("azure_openai_endpoint") or "").strip() or None
        resource_group = str(row.get("resource_group") or "").strip() or None
        deployment_name = str(row.get("deployment_name") or "").strip() or None
        owner_tag = person_from_payload(row)

        existing = await session.execute(
            select(AzureOpenaiKey).where(
                AzureOpenaiKey.subscription_id == subscription_id,
                AzureOpenaiKey.resource_name == resource_name,
            )
        )
        stored = existing.scalar_one_or_none()
        if stored is None:
            session.add(
                AzureOpenaiKey(
                    subscription_id=subscription_id,
                    resource_name=resource_name,
                    resource_group=resource_group,
                    endpoint=endpoint,
                    deployment_name=deployment_name,
                    api_key_encrypted=encrypted,
                    owner_tag=owner_tag,
                )
            )
        else:
            stored.api_key_encrypted = encrypted
            if resource_group:
                stored.resource_group = resource_group
            if endpoint:
                stored.endpoint = endpoint
            if deployment_name:
                stored.deployment_name = deployment_name
            if owner_tag:
                stored.owner_tag = owner_tag

        accounts = (
            await session.execute(select(ProviderAccount).where(ProviderAccount.subscription_id == subscription_id))
        ).scalars()
        target = resource_key(resource_name)
        endpoint_key = resource_key(endpoint)
        for account in accounts:
            if resource_key(account.resource_name) == target or (
                endpoint_key and resource_key(account.endpoint) == endpoint_key
            ):
                account.openai_api_key_encrypted = encrypted
                if owner_tag and not account.owner_tag:
                    account.owner_tag = owner_tag
        wrote = True
    if wrote:
        await session.commit()


async def attach_stored_key(session: AsyncSession, account: ProviderAccount) -> None:
    """Copy a previously stored Foundry key onto a newly created monitoring account."""
    name_key = resource_key(account.resource_name)
    if not account.subscription_id or not name_key:
        return
    result = await session.execute(
        select(AzureOpenaiKey).where(AzureOpenaiKey.subscription_id == account.subscription_id)
    )
    for stored in result.scalars():
        if resource_key(stored.resource_name) == name_key or (
            stored.endpoint and resource_key(stored.endpoint) == resource_key(account.endpoint)
        ):
            account.openai_api_key_encrypted = stored.api_key_encrypted
            if stored.owner_tag and not account.owner_tag:
                account.owner_tag = stored.owner_tag
            return


def decrypt_openai_api_key(account: ProviderAccount, box: SecretBox | None = None) -> str | None:
    if not account.openai_api_key_encrypted:
        return None
    box = box or get_secret_box()
    return box.decrypt(account.openai_api_key_encrypted)


async def decrypt_foundry_key(
    session: AsyncSession,
    subscription_id: str,
    resource_name: str,
    box: SecretBox | None = None,
) -> str | None:
    """Plaintext Foundry key for NewAPI channel create. Caller must not log or return it."""
    subscription_id = (subscription_id or "").strip()
    target = resource_key(resource_name)
    if not subscription_id or not target:
        return None
    box = box or get_secret_box()
    stored_rows = (
        await session.execute(select(AzureOpenaiKey).where(AzureOpenaiKey.subscription_id == subscription_id))
    ).scalars()
    for stored in stored_rows:
        if resource_key(stored.resource_name) == target or (
            stored.endpoint and resource_key(stored.endpoint) == target
        ):
            return box.decrypt(stored.api_key_encrypted)
    accounts = (
        await session.execute(select(ProviderAccount).where(ProviderAccount.subscription_id == subscription_id))
    ).scalars()
    for account in accounts:
        if resource_key(account.resource_name) == target or resource_key(account.endpoint) == target:
            return decrypt_openai_api_key(account, box)
    return None


async def foundry_resource_names(session: AsyncSession, subscription_id: str) -> list[str]:
    subscription_id = (subscription_id or "").strip()
    if not subscription_id:
        return []
    names: list[str] = []
    seen: set[str] = set()
    stored_rows = (
        await session.execute(select(AzureOpenaiKey).where(AzureOpenaiKey.subscription_id == subscription_id))
    ).scalars()
    for stored in stored_rows:
        key = resource_key(stored.resource_name) or resource_key(stored.endpoint)
        if key and key not in seen:
            seen.add(key)
            names.append(stored.resource_name or key)
    return names
