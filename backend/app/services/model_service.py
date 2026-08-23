from app.models.model_catalog import MonitoredModel
from app.repositories.account_repository import AccountRepository
from app.repositories.model_repository import ModelRepository
from app.repositories.registered_model_repository import RegisteredModelRepository
from app.schemas.model import ModelCreateRequest, ModelUpdateRequest


class ModelNotFoundError(Exception):
    pass


class DuplicateModelError(Exception):
    """Raised when a deployment is already being monitored for an account -
    relink or edit it instead of adding it again.
    """

    pass


class RegisteredModelRequiredError(Exception):
    """Raised when the chosen registered_model_id does not exist - the model
    must be registered before a deployment can be linked to it.
    """

    pass


class ModelService:
    def __init__(
        self,
        model_repository: ModelRepository,
        account_repository: AccountRepository,
        registered_model_repository: RegisteredModelRepository,
    ) -> None:
        self._model_repository = model_repository
        self._account_repository = account_repository
        self._registered_model_repository = registered_model_repository

    async def list_models(self) -> list[dict]:
        models = await self._model_repository.list_all()
        account_names = await self._account_name_lookup()
        return [self._serialize(model, account_names) for model in models]

    async def create_model(self, payload: ModelCreateRequest) -> dict:
        existing = await self._model_repository.find_by_account_and_deployment(
            payload.provider_account_id, payload.deployment_name
        )
        if existing is not None:
            raise DuplicateModelError(
                f"'{payload.deployment_name}' is already monitored for this account. "
                "Edit it from the Models table instead of adding it again."
            )
        registered_model = await self._registered_model_repository.get(payload.registered_model_id)
        if registered_model is None:
            raise RegisteredModelRequiredError("Register this model with a name and price before linking it.")

        model = await self._model_repository.create(MonitoredModel(**payload.model_dump()))
        return self._serialize(model, await self._account_name_lookup())

    async def update_model(self, model_id: int, payload: ModelUpdateRequest) -> dict:
        model = await self._get_or_raise(model_id)
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(model, field, value)
        model = await self._model_repository.save(model)
        return self._serialize(model, await self._account_name_lookup())

    async def delete_model(self, model_id: int) -> None:
        model = await self._get_or_raise(model_id)
        await self._model_repository.delete(model)

    async def _get_or_raise(self, model_id: int) -> MonitoredModel:
        model = await self._model_repository.get(model_id)
        if model is None:
            raise ModelNotFoundError(f"Model {model_id} not found")
        return model

    async def _account_name_lookup(self) -> dict[int, str]:
        return {account.id: account.name for account in await self._account_repository.list_all()}

    @staticmethod
    def _serialize(model: MonitoredModel, account_names: dict[int, str]) -> dict:
        registered = model.registered_model
        return {
            "id": model.id,
            "provider_account_id": model.provider_account_id,
            "provider_account_name": account_names.get(model.provider_account_id, ""),
            "deployment_name": model.deployment_name,
            "registered_model_id": model.registered_model_id,
            "model_name": registered.name,
            "input_price_per_million": registered.input_price_per_million,
            "cached_input_price_per_million": registered.cached_input_price_per_million,
            "output_price_per_million": registered.output_price_per_million,
            "currency": registered.currency,
            "enabled": model.enabled,
            "created_at": model.created_at,
        }
