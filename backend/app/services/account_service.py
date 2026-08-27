from sqlalchemy.exc import IntegrityError

from app.core.crypto import SecretBox
from app.models.model_catalog import MonitoredModel
from app.models.provider_account import ProviderAccount
from app.providers.base import ProviderCredentials
from app.providers.registry import get_provider
from app.repositories.account_repository import AccountRepository
from app.repositories.model_repository import ModelRepository
from app.repositories.registered_model_repository import RegisteredModelRepository
from app.schemas.account import (
    AccountCreateRequest,
    AccountDiscoverDeploymentsRequest,
    AccountDiscoverRequest,
    AccountUpdateRequest,
)
from app.services.openai_key_store import attach_stored_key
from app.services.owner_tag import apply_owner_to_account, parse_owner_tag, person_from_payload


class AccountNotFoundError(Exception):
    pass


class DuplicateAccountError(Exception):
    pass


class AccountValidationError(Exception):
    pass


def allocate_unique_name(preferred: str, taken_lower: set[str]) -> str:
    """Return preferred, or preferred1 / preferred2 / … if that name is already used."""
    cleaned = " ".join((preferred or "").split())[:128] or "account"
    if cleaned.lower() not in taken_lower:
        return cleaned
    for index in range(1, 1000):
        suffix = str(index)
        candidate = f"{cleaned[: 128 - len(suffix)]}{suffix}"
        if candidate.lower() not in taken_lower:
            return candidate
    raise AccountValidationError("Could not allocate a unique account name.")


class AccountService:
    """CRUD and discovery for provider accounts. Sync orchestration lives separately
    in SyncOrchestrator to keep this class focused on account management only.
    """

    def __init__(self, account_repository: AccountRepository, secret_box: SecretBox) -> None:
        self._account_repository = account_repository
        self._secret_box = secret_box

    async def discover(self, payload: AccountDiscoverRequest) -> list[dict]:
        provider = get_provider("azure_openai")
        credentials = ProviderCredentials(
            tenant_id=payload.tenant_id,
            client_id=payload.client_id,
            client_secret=payload.client_secret,
            subscription_id=payload.subscription_id,
        )
        resources = await provider.discover_resources(credentials)
        return [resource.__dict__ for resource in resources]

    async def discover_deployments(self, payload: AccountDiscoverDeploymentsRequest) -> list[dict]:
        provider = get_provider("azure_openai")
        credentials = ProviderCredentials(
            tenant_id=payload.tenant_id,
            client_id=payload.client_id,
            client_secret=payload.client_secret,
            subscription_id=payload.subscription_id,
        )
        deployments = await provider.list_deployments(credentials, payload.resource_id)
        return [deployment.__dict__ for deployment in deployments]

    async def create_account(self, payload: AccountCreateRequest) -> ProviderAccount:
        resource = _filled_resource(
            subscription_id=payload.subscription_id,
            resource_name=payload.resource_name,
            resource_id=payload.resource_id,
            resource_group=payload.resource_group,
            endpoint=payload.endpoint,
            kind=payload.kind,
            location=payload.location,
        )
        name = await _unique_account_name(
            self._account_repository,
            preferred=payload.name,
            resource_name=payload.resource_name,
            subscription_id=payload.subscription_id,
        )
        account = ProviderAccount(
            name=name,
            provider_type="azure_openai",
            tenant_id=payload.tenant_id,
            client_id=payload.client_id,
            client_secret_encrypted=self._secret_box.encrypt(payload.client_secret),
            subscription_id=payload.subscription_id,
            resource_id=resource["resource_id"],
            resource_group=resource["resource_group"],
            resource_name=resource["resource_name"],
            endpoint=resource["endpoint"],
            kind=resource["kind"],
            location=resource["location"],
        )
        _apply_credit_grant(account, payload.credits_limit, manual=True)
        try:
            account.owner_tag = parse_owner_tag(payload.owner_tag)
        except ValueError as exc:
            raise AccountValidationError(str(exc)) from exc
        siblings = await self._account_repository.list_all()
        apply_owner_to_account(account, [*siblings, account])
        await attach_stored_key(self._account_repository._session, account)
        try:
            return await self._account_repository.create(account)
        except IntegrityError:
            account.name = await _unique_account_name(
                self._account_repository,
                preferred=payload.name,
                resource_name=payload.resource_name,
                subscription_id=payload.subscription_id,
            )
            return await self._account_repository.create(account)

    async def upsert_from_kimi_deploy(
        self,
        *,
        payload: dict[str, str],
        resource_name: str,
        resource_group: str,
        endpoint: str,
        location: str,
        owner_tag: str | None,
        credits_limit: float | None,
        credits_remaining: float | None,
        credits_used: float | None,
        credits_currency: str | None,
        credits_label: str | None,
        deployment_name: str | None,
    ) -> ProviderAccount:
        """Create or refresh the portal monitoring account for a successful Kimi deploy."""
        subscription_id = (payload.get("AZURE_SUBSCRIPTION_ID") or "").strip()
        tenant_id = (payload.get("AZURE_TENANT_ID") or "").strip()
        client_id = (payload.get("AZURE_CLIENT_ID") or "").strip()
        client_secret = (payload.get("AZURE_CLIENT_SECRET") or "").strip()
        if not subscription_id or not tenant_id or not client_id or not client_secret or not resource_name:
            raise AccountValidationError("Deploy result is missing credentials or Foundry resource name.")

        resource = _filled_resource(
            subscription_id=subscription_id,
            resource_name=resource_name,
            resource_group=resource_group,
            endpoint=endpoint,
            kind="AIServices",
            location=location,
        )
        account = await self._account_repository.get_by_subscription_and_resource(subscription_id, resource_name)
        created = account is None
        if account is None:
            name = await _unique_account_name(
                self._account_repository,
                preferred=payload.get("name") or payload.get("account_holder") or resource_name,
                resource_name=resource_name,
                subscription_id=subscription_id,
            )
            account = ProviderAccount(
                name=name,
                provider_type="azure_openai",
                tenant_id=tenant_id,
                client_id=client_id,
                client_secret_encrypted=self._secret_box.encrypt(client_secret),
                subscription_id=subscription_id,
                resource_id=resource["resource_id"],
                resource_group=resource["resource_group"],
                resource_name=resource["resource_name"],
                endpoint=resource["endpoint"],
                kind=resource["kind"],
                location=resource["location"],
            )
        else:
            account.tenant_id = tenant_id
            account.client_id = client_id
            account.client_secret_encrypted = self._secret_box.encrypt(client_secret)
            for key, value in resource.items():
                setattr(account, key, value)

        try:
            # Only JSON person_associated may overwrite; owner_tag arg can be a portal copy.
            tag = person_from_payload(payload)
        except ValueError as exc:
            raise AccountValidationError(str(exc)) from exc
        if tag:
            account.owner_tag = tag
        siblings = await self._account_repository.list_all()
        apply_owner_to_account(account, siblings)

        if credits_limit and not account.credits_limit_manual:
            _apply_credit_grant(account, credits_limit, manual=False)
            if credits_remaining is not None:
                account.credits_remaining = credits_remaining
            if credits_used is not None:
                account.credits_used = credits_used
            if credits_currency:
                account.credits_currency = credits_currency
            if credits_label:
                account.credits_label = credits_label

        await attach_stored_key(self._account_repository._session, account)
        if created:
            try:
                account = await self._account_repository.create(account)
            except IntegrityError:
                account.name = await _unique_account_name(
                    self._account_repository,
                    preferred=payload.get("name") or payload.get("account_holder") or resource_name,
                    resource_name=resource_name,
                    subscription_id=subscription_id,
                )
                account = await self._account_repository.create(account)
        else:
            account = await self._account_repository.save(account)

        await _link_kimi_deployment(
            self._account_repository._session,
            account.id,
            deployment_name or "FW-Kimi-K3",
        )
        return account

    async def list_accounts(self) -> list[ProviderAccount]:
        return await self._account_repository.list_all()

    async def list_deployments(self, account_id: int) -> list[dict]:
        account = await self._get_or_raise(account_id)
        return await self.discover_deployments_for_account(account_id, account.resource_id)

    async def discover_deployments_for_account(self, account_id: int, resource_id: str) -> list[dict]:
        account = await self._get_or_raise(account_id)
        provider = get_provider(account.provider_type)
        deployments = await provider.list_deployments(self._credentials_for(account), resource_id)
        return [deployment.__dict__ for deployment in deployments]

    async def test_connection(self, account_id: int) -> dict:
        account = await self._get_or_raise(account_id)
        provider = get_provider(account.provider_type)
        try:
            await provider.list_deployments(self._credentials_for(account), account.resource_id)
            return {"status": "ok"}
        except Exception as exc:  # noqa: BLE001 - surfaced directly to the admin UI
            return {"status": "error", "detail": str(exc)}

    async def discover_for_account(self, account_id: int) -> list[dict]:
        account = await self._get_or_raise(account_id)
        provider = get_provider(account.provider_type)
        resources = await provider.discover_resources(self._credentials_for(account))
        return [resource.__dict__ for resource in resources]

    async def update_account(self, account_id: int, payload: AccountUpdateRequest) -> ProviderAccount:
        account = await self._get_or_raise(account_id)
        if payload.name is not None and payload.name != account.name:
            existing = await self._account_repository.get_by_name(payload.name)
            if existing is not None:
                raise DuplicateAccountError("An account with that name already exists.")
        resource_fields = {
            "resource_id": payload.resource_id if payload.resource_id is not None else account.resource_id,
            "resource_group": payload.resource_group if payload.resource_group is not None else account.resource_group,
            "resource_name": payload.resource_name if payload.resource_name is not None else account.resource_name,
            "endpoint": payload.endpoint if payload.endpoint is not None else account.endpoint,
            "kind": payload.kind if payload.kind is not None else account.kind,
            "location": payload.location if payload.location is not None else account.location,
        }
        if any(
            getattr(payload, field) is not None
            for field in ("resource_id", "resource_group", "resource_name", "endpoint", "kind", "location")
        ):
            filled = _filled_resource(subscription_id=account.subscription_id, **resource_fields)
            for key, value in filled.items():
                setattr(account, key, value)
        if payload.name is not None:
            account.name = payload.name
        if payload.credits_limit is not None:
            _apply_credit_grant(account, payload.credits_limit, manual=True)
        if payload.credits_limit_manual is not None:
            account.credits_limit_manual = payload.credits_limit_manual
        if payload.owner_tag is not None:
            try:
                account.owner_tag = parse_owner_tag(payload.owner_tag)
            except ValueError as exc:
                raise AccountValidationError(str(exc)) from exc
        else:
            apply_owner_to_account(account, await self._account_repository.list_all())
        try:
            return await self._account_repository.save(account)
        except IntegrityError as exc:
            raise DuplicateAccountError("An account with that name already exists.") from exc

    async def delete_account(self, account_id: int) -> None:
        account = await self._get_or_raise(account_id)
        await self._account_repository.delete(account)

    async def _get_or_raise(self, account_id: int) -> ProviderAccount:
        account = await self._account_repository.get(account_id)
        if account is None:
            raise AccountNotFoundError(f"Account {account_id} not found")
        return account

    def _credentials_for(self, account: ProviderAccount) -> ProviderCredentials:
        return ProviderCredentials(
            tenant_id=account.tenant_id,
            client_id=account.client_id,
            client_secret=self._secret_box.decrypt(account.client_secret_encrypted),
            subscription_id=account.subscription_id,
        )


def _filled_resource(
    *,
    subscription_id: str,
    resource_name: str,
    resource_id: str = "",
    resource_group: str = "",
    endpoint: str = "",
    kind: str = "",
    location: str = "",
) -> dict[str, str]:
    name = (resource_name or "").strip()
    if not name:
        raise AccountValidationError("Resource name is required.")
    group = (resource_group or "").strip() or "manual"
    kind_value = (kind or "").strip() or "AIServices"
    location_value = (location or "").strip()
    endpoint_value = (endpoint or "").strip()
    if not endpoint_value:
        endpoint_value = f"https://{name}.cognitiveservices.azure.com/"
    resource_id_value = (resource_id or "").strip()
    if not resource_id_value:
        resource_id_value = (
            f"/subscriptions/{subscription_id}/resourceGroups/{group}"
            f"/providers/Microsoft.CognitiveServices/accounts/{name}"
        )
    return {
        "resource_name": name,
        "resource_group": group,
        "kind": kind_value,
        "location": location_value,
        "endpoint": endpoint_value,
        "resource_id": resource_id_value,
    }


def _apply_credit_grant(account: ProviderAccount, limit: float | None, *, manual: bool) -> None:
    if limit is None:
        return
    if limit <= 0:
        raise AccountValidationError("Credit grant must be greater than 0.")
    account.credits_limit = float(limit)
    if account.credits_remaining is None or account.credits_remaining > limit:
        account.credits_remaining = float(limit)
    if account.credits_used is None:
        account.credits_used = 0.0
    account.credits_currency = account.credits_currency or "USD"
    account.credits_unit = "currency"
    account.credits_label = "Manual credit grant" if manual else (account.credits_label or "Credits")
    account.credits_available = True
    if manual:
        account.credits_limit_manual = True


async def _unique_account_name(
    repo: AccountRepository,
    *,
    preferred: str,
    resource_name: str,
    subscription_id: str,
) -> str:
    cleaned = " ".join((preferred or "").split())[:128]
    resource_name = (resource_name or "").strip()
    taken: set[str] = set()
    reuse: str | None = None
    for row in await repo.list_all():
        same_resource = bool(
            resource_name
            and (row.subscription_id or "") == subscription_id
            and (row.resource_name or "").lower() == resource_name.lower()
        )
        if same_resource:
            reuse = row.name
            continue
        if row.name:
            taken.add(row.name.lower())
    if reuse:
        return reuse
    return allocate_unique_name(cleaned or resource_name or f"kimi-{subscription_id[:8]}", taken)


async def _link_kimi_deployment(session, account_id: int, deployment_name: str) -> None:
    registered = await RegisteredModelRepository(session).get_by_name(deployment_name)
    if registered is None:
        return
    models = ModelRepository(session)
    existing = await models.find_by_account_and_deployment(account_id, deployment_name)
    if existing is not None:
        return
    await models.create(
        MonitoredModel(
            provider_account_id=account_id,
            registered_model_id=registered.id,
            deployment_name=deployment_name,
            enabled=True,
        )
    )
