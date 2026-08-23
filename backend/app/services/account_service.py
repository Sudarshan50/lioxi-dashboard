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


class AccountNotFoundError(Exception):
    pass


class DuplicateAccountError(Exception):
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
        account = ProviderAccount(
            name=payload.name,
            provider_type="azure_openai",
            tenant_id=payload.tenant_id,
            client_id=payload.client_id,
            client_secret_encrypted=self._secret_box.encrypt(payload.client_secret),
            subscription_id=payload.subscription_id,
            resource_id=payload.resource_id,
            resource_group=payload.resource_group,
            resource_name=payload.resource_name,
            endpoint=payload.endpoint,
            kind=payload.kind,
            location=payload.location,
        )
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
        for field in ("name", "resource_id", "resource_group", "resource_name", "endpoint", "kind", "location"):
            value = getattr(payload, field)
            if value is not None:
                setattr(account, field, value)
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
