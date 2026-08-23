from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.model_catalog import MonitoredModel
from app.models.registered_model import RegisteredModel


class RegisteredModelRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self) -> list[RegisteredModel]:
        result = await self._session.execute(select(RegisteredModel).order_by(RegisteredModel.name))
        return list(result.scalars())

    async def get(self, registered_model_id: int) -> RegisteredModel | None:
        return await self._session.get(RegisteredModel, registered_model_id)

    async def get_by_name(self, name: str) -> RegisteredModel | None:
        result = await self._session.execute(
            select(RegisteredModel).where(func.lower(RegisteredModel.name) == name.lower())
        )
        return result.scalar_one_or_none()

    async def create(self, registered_model: RegisteredModel) -> RegisteredModel:
        self._session.add(registered_model)
        await self._session.commit()
        await self._session.refresh(registered_model)
        return registered_model

    async def save(self, registered_model: RegisteredModel) -> RegisteredModel:
        await self._session.commit()
        await self._session.refresh(registered_model)
        return registered_model

    async def delete(self, registered_model: RegisteredModel) -> None:
        await self._session.delete(registered_model)
        await self._session.commit()

    async def usage_count(self, registered_model_id: int) -> int:
        result = await self._session.execute(
            select(func.count(MonitoredModel.id)).where(MonitoredModel.registered_model_id == registered_model_id)
        )
        return result.scalar_one()
