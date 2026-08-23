from app.core.security import create_access_token, verify_password
from app.repositories.admin_repository import AdminRepository


class InvalidCredentialsError(Exception):
    pass


class AuthService:
    def __init__(self, admin_repository: AdminRepository) -> None:
        self._admin_repository = admin_repository

    async def login(self, username: str, password: str) -> str:
        admin = await self._admin_repository.get_by_username(username)
        if admin is None or not verify_password(password, admin.password_hash):
            raise InvalidCredentialsError("Invalid username or password")
        return create_access_token(subject=admin.username)
