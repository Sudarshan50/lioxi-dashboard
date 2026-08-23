from app.models.registered_model import RegisteredModel
from app.repositories.registered_model_repository import RegisteredModelRepository
from app.schemas.registered_model import RegisteredModelCreateRequest, RegisteredModelUpdateRequest


class RegisteredModelNotFoundError(Exception):
    pass


class DuplicateRegisteredModelError(Exception):
    """A model name must be registered exactly once - re-registering it would
    fragment its pricing/history across multiple identities again."""

    pass


class RegisteredModelInUseError(Exception):
    """Raised when trying to delete a registered model that deployments still
    link to; unlink or delete those first."""

    pass


class RegisteredModelService:
    def __init__(self, repository: RegisteredModelRepository) -> None:
        self._repository = repository

    async def list_models(self) -> list[dict]:
        models = await self._repository.list_all()
        return [await self._serialize(model) for model in models]

    async def register(self, payload: RegisteredModelCreateRequest) -> dict:
        existing = await self._repository.get_by_name(payload.name)
        if existing is not None:
            raise DuplicateRegisteredModelError(
                f"'{payload.name}' is already registered. Use it directly instead of registering it again."
            )
        model = await self._repository.create(RegisteredModel(**payload.model_dump()))
        return await self._serialize(model)

    async def update(self, registered_model_id: int, payload: RegisteredModelUpdateRequest) -> dict:
        model = await self._get_or_raise(registered_model_id)
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(model, field, value)
        model = await self._repository.save(model)
        return await self._serialize(model)

    async def delete(self, registered_model_id: int) -> None:
        model = await self._get_or_raise(registered_model_id)
        usage = await self._repository.usage_count(registered_model_id)
        if usage > 0:
            raise RegisteredModelInUseError(
                f"'{model.name}' is still linked from {usage} deployment(s). Remove those first."
            )
        await self._repository.delete(model)

    async def _get_or_raise(self, registered_model_id: int) -> RegisteredModel:
        model = await self._repository.get(registered_model_id)
        if model is None:
            raise RegisteredModelNotFoundError(f"Registered model {registered_model_id} not found")
        return model

    async def _serialize(self, model: RegisteredModel) -> dict:
        return {
            "id": model.id,
            "name": model.name,
            "input_price_per_million": model.input_price_per_million,
            "cached_input_price_per_million": model.cached_input_price_per_million,
            "output_price_per_million": model.output_price_per_million,
            "currency": model.currency,
            "deployments_count": await self._repository.usage_count(model.id),
            "created_at": model.created_at,
            "updated_at": model.updated_at,
        }
