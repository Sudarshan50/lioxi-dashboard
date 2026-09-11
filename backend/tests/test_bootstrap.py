import unittest
from unittest.mock import AsyncMock, patch

from app.services.bootstrap import ensure_admin_seeded


class _Admin:
    def __init__(self, username: str, password_hash: str) -> None:
        self.username = username
        self.password_hash = password_hash


class EnsureAdminSeeded(unittest.IsolatedAsyncioTestCase):
    async def test_creates_missing_admin(self):
        repo = AsyncMock()
        repo.get_by_username.return_value = None
        with patch("app.services.bootstrap.hash_password", return_value="hashed-new"):
            await ensure_admin_seeded(repo, "admin", "new-pass")
        repo.create.assert_awaited_once()
        created = repo.create.await_args.args[0]
        self.assertEqual(created.username, "admin")
        self.assertEqual(created.password_hash, "hashed-new")
        repo.update_password.assert_not_called()

    async def test_updates_hash_when_env_password_changed(self):
        admin = _Admin("admin", "old-hash")
        repo = AsyncMock()
        repo.get_by_username.return_value = admin
        with patch("app.services.bootstrap.verify_password", return_value=False):
            with patch("app.services.bootstrap.hash_password", return_value="hashed-rotated"):
                await ensure_admin_seeded(repo, "admin", "rotated-pass")
        repo.create.assert_not_called()
        repo.update_password.assert_awaited_once_with(admin, "hashed-rotated")

    async def test_leaves_matching_password_alone(self):
        admin = _Admin("admin", "current-hash")
        repo = AsyncMock()
        repo.get_by_username.return_value = admin
        with patch("app.services.bootstrap.verify_password", return_value=True):
            await ensure_admin_seeded(repo, "admin", "same-pass")
        repo.create.assert_not_called()
        repo.update_password.assert_not_called()


if __name__ == "__main__":
    unittest.main()
