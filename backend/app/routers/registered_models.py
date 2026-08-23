from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_admin
from app.repositories.registered_model_repository import RegisteredModelRepository
from app.schemas.registered_model import (
    RegisteredModelCreateRequest,
    RegisteredModelResponse,
    RegisteredModelUpdateRequest,
)
from app.services.registered_model_service import (
    DuplicateRegisteredModelError,
    RegisteredModelInUseError,
    RegisteredModelNotFoundError,
    RegisteredModelService,
)

router = APIRouter(prefix="/api/registered-models", tags=["registered-models"], dependencies=[Depends(get_current_admin)])


def _service(db: AsyncSession = Depends(get_db)) -> RegisteredModelService:
    return RegisteredModelService(RegisteredModelRepository(db))


@router.get("", response_model=list[RegisteredModelResponse])
async def list_registered_models(service: RegisteredModelService = Depends(_service)):
    return await service.list_models()


@router.post("", response_model=RegisteredModelResponse)
async def register_model(payload: RegisteredModelCreateRequest, service: RegisteredModelService = Depends(_service)):
    try:
        return await service.register(payload)
    except DuplicateRegisteredModelError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.patch("/{registered_model_id}", response_model=RegisteredModelResponse)
async def update_registered_model(
    registered_model_id: int, payload: RegisteredModelUpdateRequest, service: RegisteredModelService = Depends(_service)
):
    try:
        return await service.update(registered_model_id, payload)
    except RegisteredModelNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/{registered_model_id}")
async def delete_registered_model(registered_model_id: int, service: RegisteredModelService = Depends(_service)):
    try:
        await service.delete(registered_model_id)
    except RegisteredModelNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RegisteredModelInUseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "deleted"}
