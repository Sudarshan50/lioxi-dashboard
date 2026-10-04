import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.account_service import AccountNotFoundError
from app.services.alert_service import set_blocked
from app.services.new_api_service import NewApiError, _set_gateway_status_locked


def _account(**kwargs):
    defaults = dict(
        id=11,
        name="Alex",
        new_api_status=2,
        blocked=False,
        endpoint="https://alex.example",
        resource_name="alex-kimi",
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class SetBlocked(unittest.IsolatedAsyncioTestCase):
    async def test_tags_disabled_account(self):
        account = _account()
        session = AsyncMock()
        repo = SimpleNamespace(get=AsyncMock(return_value=account), save=AsyncMock(return_value=account))
        with patch("app.services.alert_service.AccountRepository", return_value=repo):
            result = await set_blocked(session, 11, True)
        self.assertTrue(account.blocked)
        self.assertEqual(result, {"id": 11, "blocked": True})
        repo.save.assert_awaited_once_with(account)

    async def test_rejects_enabled_account(self):
        account = _account(new_api_status=1)
        session = AsyncMock()
        repo = SimpleNamespace(get=AsyncMock(return_value=account), save=AsyncMock())
        with patch("app.services.alert_service.AccountRepository", return_value=repo):
            with self.assertRaises(ValueError) as ctx:
                await set_blocked(session, 11, True)
        self.assertIn("disabled", str(ctx.exception))
        repo.save.assert_not_awaited()
        self.assertFalse(account.blocked)

    async def test_untag_clears_flag(self):
        account = _account(new_api_status=1, blocked=True)
        session = AsyncMock()
        repo = SimpleNamespace(get=AsyncMock(return_value=account), save=AsyncMock(return_value=account))
        with patch("app.services.alert_service.AccountRepository", return_value=repo):
            result = await set_blocked(session, 11, False)
        self.assertFalse(account.blocked)
        self.assertEqual(result["blocked"], False)

    async def test_unknown_account(self):
        session = AsyncMock()
        repo = SimpleNamespace(get=AsyncMock(return_value=None), save=AsyncMock())
        with patch("app.services.alert_service.AccountRepository", return_value=repo):
            with self.assertRaises(AccountNotFoundError):
                await set_blocked(session, 99, True)


class EnableBlocked(unittest.IsolatedAsyncioTestCase):
    async def test_refuses_enable(self):
        account = _account(blocked=True, new_api_status=2)
        session = AsyncMock()
        repo = SimpleNamespace(get=AsyncMock(return_value=account))
        with patch("app.services.new_api_service.AccountRepository", return_value=repo):
            with self.assertRaises(NewApiError) as ctx:
                await _set_gateway_status_locked(session, 11, 1, None)
        self.assertIn("blocked", str(ctx.exception).lower())
