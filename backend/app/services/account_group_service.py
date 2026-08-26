from app.models.account_group import AccountGroup
from app.models.provider_account import ProviderAccount
from app.repositories.account_group_repository import AccountGroupRepository
from app.repositories.account_repository import AccountRepository
from app.schemas.account_group import AccountGroupCreateRequest, AccountGroupUpdateRequest

ONE_K_GROUP_NAME = "1k Accounts"
ONE_K_GRANT_MIN = 800
ONE_K_GRANT_MAX = 1500


class AccountGroupNotFoundError(Exception):
    pass


class DuplicateAccountGroupError(Exception):
    pass


class AccountGroupService:
    def __init__(self, group_repository: AccountGroupRepository, account_repository: AccountRepository) -> None:
        self._group_repository = group_repository
        self._account_repository = account_repository

    async def list_groups(self) -> list[dict]:
        await self.ensure_1k_group()
        groups = await self._group_repository.list_all()
        if not groups:
            return []
        members = await self._group_repository.member_account_ids_bulk([g.id for g in groups])
        account_names = await self._account_name_lookup()
        return [self._serialize(group, members.get(group.id, []), account_names) for group in groups]

    async def create_group(self, payload: AccountGroupCreateRequest) -> dict:
        existing = await self._group_repository.get_by_name(payload.name)
        if existing is not None:
            raise DuplicateAccountGroupError(f"A group named '{payload.name}' already exists.")
        group = await self._group_repository.create(AccountGroup(name=payload.name))
        await self._group_repository.set_members(group.id, payload.account_ids)
        return await self._get_serialized(group.id)

    async def update_group(self, group_id: int, payload: AccountGroupUpdateRequest) -> dict:
        group = await self._get_or_raise(group_id)
        if payload.name is not None:
            group.name = payload.name
            await self._group_repository.save(group)
        if payload.account_ids is not None:
            await self._group_repository.set_members(group_id, payload.account_ids)
        return await self._get_serialized(group_id)

    async def delete_group(self, group_id: int) -> None:
        group = await self._get_or_raise(group_id)
        await self._group_repository.delete(group)

    async def ensure_1k_group(self) -> dict:
        """Keep the 1k Accounts group in sync with every ~$1,000 grant."""
        accounts = await self._account_repository.list_all()
        member_ids = [account.id for account in accounts if is_1k_account(account)]
        group = await self._group_repository.get_by_name(ONE_K_GROUP_NAME)
        if group is None:
            group = await self._group_repository.create(AccountGroup(name=ONE_K_GROUP_NAME))
        current = set(await self._group_repository.member_account_ids(group.id))
        if current != set(member_ids):
            await self._group_repository.set_members(group.id, member_ids)
        return await self._get_serialized(group.id)

    async def member_account_ids(self, group_id: int) -> list[int]:
        await self._get_or_raise(group_id)
        return await self._group_repository.member_account_ids(group_id)

    async def _get_or_raise(self, group_id: int) -> AccountGroup:
        group = await self._group_repository.get(group_id)
        if group is None:
            raise AccountGroupNotFoundError(f"Account group {group_id} not found")
        return group

    async def _get_serialized(self, group_id: int) -> dict:
        group = await self._get_or_raise(group_id)
        member_ids = await self._group_repository.member_account_ids(group_id)
        account_names = await self._account_name_lookup()
        return self._serialize(group, member_ids, account_names)

    async def _account_name_lookup(self) -> dict[int, str]:
        return {account.id: account.name for account in await self._account_repository.list_all()}

    @staticmethod
    def _serialize(group: AccountGroup, member_ids: list[int], account_names: dict[int, str]) -> dict:
        return {
            "id": group.id,
            "name": group.name,
            "accounts": [
                {"id": account_id, "name": account_names.get(account_id, "")}
                for account_id in member_ids
                if account_id in account_names
            ],
            "created_at": group.created_at,
            "auto": group.name.lower() == ONE_K_GROUP_NAME.lower(),
        }


def is_1k_account(account: ProviderAccount) -> bool:
    compact = account.name.lower().replace(" ", "")
    if "1k" in compact or compact.endswith("-1k") or compact.endswith("_1k"):
        return True
    limit = account.credits_limit
    if limit is None:
        return False
    if (account.credits_currency or "USD").upper() != "USD":
        return False
    return ONE_K_GRANT_MIN <= float(limit) <= ONE_K_GRANT_MAX
