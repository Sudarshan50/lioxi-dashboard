from app.core.security import hash_password, verify_password
from app.models.admin import AdminAccount
from app.repositories.admin_repository import AdminRepository


async def ensure_admin_seeded(admin_repository: AdminRepository, username: str, password: str) -> None:
    admin = await admin_repository.get_by_username(username)
    if admin is None:
        await admin_repository.create(AdminAccount(username=username, password_hash=hash_password(password)))
        return
    if not verify_password(password, admin.password_hash):
        await admin_repository.update_password(admin, hash_password(password))
