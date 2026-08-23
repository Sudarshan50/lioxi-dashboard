from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.model_catalog import MonitoredModel
from app.models.registered_model import RegisteredModel


class ModelRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self) -> list[MonitoredModel]:
        result = await self._session.execute(
            select(MonitoredModel).join(RegisteredModel).order_by(RegisteredModel.name)
        )
        return list(result.scalars())

    async def list_by_account(self, account_id: int) -> list[MonitoredModel]:
        result = await self._session.execute(
            select(MonitoredModel).where(MonitoredModel.provider_account_id == account_id)
        )
        return list(result.scalars())

    async def get(self, model_id: int) -> MonitoredModel | None:
        return await self._session.get(MonitoredModel, model_id)

    async def find_by_account_and_deployment(self, account_id: int, deployment_name: str) -> MonitoredModel | None:
        result = await self._session.execute(
            select(MonitoredModel).where(
                MonitoredModel.provider_account_id == account_id,
                MonitoredModel.deployment_name == deployment_name,
            )
        )
        return result.scalar_one_or_none()

    async def create(self, model: MonitoredModel) -> MonitoredModel:
        self._session.add(model)
        await self._session.commit()
        await self._session.refresh(model)
        return model

    async def save(self, model: MonitoredModel) -> MonitoredModel:
        await self._session.commit()
        await self._session.refresh(model)
        return model

    async def delete(self, model: MonitoredModel) -> None:
        await self._session.delete(model)
        await self._session.commit()

    async def count(self) -> int:
        result = await self._session.execute(select(func.count(MonitoredModel.id)))
        return result.scalar_one()
