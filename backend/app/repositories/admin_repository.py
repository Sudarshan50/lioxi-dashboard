from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin import AdminAccount


class AdminRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_username(self, username: str) -> AdminAccount | None:
        result = await self._session.execute(select(AdminAccount).where(AdminAccount.username == username))
        return result.scalar_one_or_none()

    async def create(self, admin: AdminAccount) -> AdminAccount:
        self._session.add(admin)
        await self._session.commit()
        await self._session.refresh(admin)
        return admin
