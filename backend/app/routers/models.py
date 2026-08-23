from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_admin
from app.repositories.account_repository import AccountRepository
from app.repositories.model_repository import ModelRepository
from app.repositories.registered_model_repository import RegisteredModelRepository
from app.schemas.model import ModelCreateRequest, ModelResponse, ModelUpdateRequest
from app.services.model_service import DuplicateModelError, ModelNotFoundError, ModelService, RegisteredModelRequiredError

router = APIRouter(prefix="/api/models", tags=["models"], dependencies=[Depends(get_current_admin)])


def _service(db: AsyncSession = Depends(get_db)) -> ModelService:
    return ModelService(ModelRepository(db), AccountRepository(db), RegisteredModelRepository(db))


@router.get("", response_model=list[ModelResponse])
async def list_models(service: ModelService = Depends(_service)):
    return await service.list_models()


@router.post("", response_model=ModelResponse)
async def create_model(payload: ModelCreateRequest, service: ModelService = Depends(_service)):
    try:
        return await service.create_model(payload)
    except DuplicateModelError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RegisteredModelRequiredError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/{model_id}", response_model=ModelResponse)
async def update_model(model_id: int, payload: ModelUpdateRequest, service: ModelService = Depends(_service)):
    try:
        return await service.update_model(model_id, payload)
    except ModelNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/{model_id}")
async def delete_model(model_id: int, service: ModelService = Depends(_service)):
    try:
        await service.delete_model(model_id)
    except ModelNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "deleted"}
