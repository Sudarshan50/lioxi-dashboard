from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import get_secret_box
from app.core.security import decode_access_token
from app.database import get_db
from app.repositories.admin_repository import AdminRepository
from app.services.sync_orchestrator import SyncOrchestrator

_bearer_scheme = HTTPBearer()
_orchestrator: SyncOrchestrator | None = None


async def get_current_admin(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> str:
    username = decode_access_token(credentials.credentials)
    if username is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    admin = await AdminRepository(db).get_by_username(username)
    if admin is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    return username


def get_sync_orchestrator() -> SyncOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = SyncOrchestrator(get_secret_box())
    return _orchestrator
