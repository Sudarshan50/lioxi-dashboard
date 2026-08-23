from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.provider_account import ProviderAccount


class AccountRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self) -> list[ProviderAccount]:
        result = await self._session.execute(select(ProviderAccount).order_by(ProviderAccount.name))
        return list(result.scalars())

    async def get(self, account_id: int) -> ProviderAccount | None:
        return await self._session.get(ProviderAccount, account_id)

    async def get_by_name(self, name: str) -> ProviderAccount | None:
        result = await self._session.execute(select(ProviderAccount).where(ProviderAccount.name == name))
        return result.scalar_one_or_none()

    async def create(self, account: ProviderAccount) -> ProviderAccount:
        self._session.add(account)
        try:
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            raise
        await self._session.refresh(account)
        return account

    async def save(self, account: ProviderAccount) -> ProviderAccount:
        try:
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            raise
        await self._session.refresh(account)
        return account

    async def delete(self, account: ProviderAccount) -> None:
        await self._session.delete(account)
        await self._session.commit()

    async def count(self) -> int:
        result = await self._session.execute(select(func.count(ProviderAccount.id)))
        return result.scalar_one()
