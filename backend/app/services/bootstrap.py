from app.core.security import hash_password
from app.models.admin import AdminAccount
from app.repositories.admin_repository import AdminRepository


async def ensure_admin_seeded(admin_repository: AdminRepository, username: str, password: str) -> None:
    if await admin_repository.get_by_username(username) is not None:
        return
    await admin_repository.create(AdminAccount(username=username, password_hash=hash_password(password)))
