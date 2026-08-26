from sqlalchemy.exc import IntegrityError

from app.core.crypto import SecretBox
from app.models.provider_account import ProviderAccount
from app.providers.base import ProviderCredentials
from app.providers.registry import get_provider
from app.repositories.account_repository import AccountRepository
from app.schemas.account import (
    AccountCreateRequest,
    AccountDiscoverDeploymentsRequest,
    AccountDiscoverRequest,
    AccountUpdateRequest,
)
from app.services.owner_tag import apply_owner_to_account, parse_owner_tag


class AccountNotFoundError(Exception):
    pass


class DuplicateAccountError(Exception):
    pass


class AccountValidationError(Exception):
    pass


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
        existing = await self._account_repository.get_by_name(payload.name)
        if existing is not None:
            raise DuplicateAccountError("An account with that name already exists.")
        resource = _filled_resource(
            subscription_id=payload.subscription_id,
            resource_name=payload.resource_name,
            resource_id=payload.resource_id,
            resource_group=payload.resource_group,
            endpoint=payload.endpoint,
            kind=payload.kind,
            location=payload.location,
        )
        account = ProviderAccount(
            name=payload.name,
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
        try:
            return await self._account_repository.create(account)
        except IntegrityError as exc:
            raise DuplicateAccountError("An account with that name already exists.") from exc

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
