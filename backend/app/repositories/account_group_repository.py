from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account_group import AccountGroup
from app.models.account_group_member import AccountGroupMember


class AccountGroupRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self) -> list[AccountGroup]:
        result = await self._session.execute(select(AccountGroup).order_by(AccountGroup.name))
        return list(result.scalars())

    async def get(self, group_id: int) -> AccountGroup | None:
        return await self._session.get(AccountGroup, group_id)

    async def get_by_name(self, name: str) -> AccountGroup | None:
        result = await self._session.execute(
            select(AccountGroup).where(func.lower(AccountGroup.name) == name.lower())
        )
        return result.scalar_one_or_none()

    async def create(self, group: AccountGroup) -> AccountGroup:
        self._session.add(group)
        await self._session.commit()
        await self._session.refresh(group)
        return group

    async def save(self, group: AccountGroup) -> AccountGroup:
        await self._session.commit()
        await self._session.refresh(group)
        return group

    async def delete(self, group: AccountGroup) -> None:
        await self._session.delete(group)
        await self._session.commit()

    async def member_account_ids(self, group_id: int) -> list[int]:
        result = await self._session.execute(
            select(AccountGroupMember.provider_account_id).where(AccountGroupMember.account_group_id == group_id)
        )
        return list(result.scalars())

    async def member_account_ids_bulk(self, group_ids: list[int]) -> dict[int, list[int]]:
        result = await self._session.execute(
            select(AccountGroupMember.account_group_id, AccountGroupMember.provider_account_id).where(
                AccountGroupMember.account_group_id.in_(group_ids)
            )
        )
        members: dict[int, list[int]] = {group_id: [] for group_id in group_ids}
        for group_id, account_id in result.all():
            members[group_id].append(account_id)
        return members

    async def set_members(self, group_id: int, account_ids: list[int]) -> None:
        await self._session.execute(delete(AccountGroupMember).where(AccountGroupMember.account_group_id == group_id))
        for account_id in set(account_ids):
            self._session.add(AccountGroupMember(account_group_id=group_id, provider_account_id=account_id))
        await self._session.commit()
