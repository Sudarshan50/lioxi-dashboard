"""Regressions for defects found in the notification-routing audit."""

import asyncio
import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services import alert_service, telegram_bot, telegram_service


def _run(coro):
    return asyncio.run(coro)


def _account(name, group, spend, level=0, limit=10000):
    return SimpleNamespace(
        id=abs(hash(name)) % 10000,
        name=name,
        owner_tag=name,
        group_tag=group,
        new_api_name=name,
        new_api_gateway="O1",
        new_api_cost_usd=spend,
        new_api_cost_o1_usd=spend,
        new_api_cost_o2_usd=0,
        new_api_status=1,
        new_api_status_o1=1,
        new_api_status_o2=None,
        new_api_alert_level=level,
        credits_limit=limit,
        credits_currency="USD",
        credits_limit_manual=False,
    )


class ClearChatWiringTests(unittest.TestCase):
    """The router calls the bot wrapper, which must accept the group."""

    def test_bot_wrapper_accepts_the_group_argument(self):
        params = inspect.signature(telegram_bot.start_clear_group_chat).parameters
        self.assertIn("group", params)

    def test_wrapper_forwards_the_group(self):
        with patch.object(
            telegram_service, "start_clear_group_chat", new_callable=AsyncMock
        ) as started:
            _run(telegram_bot.start_clear_group_chat("vcs"))
        self.assertEqual(started.await_args.args[0], "vcs")


class _FakeTimer:
    """Stand-in for a self-destruct task; only cancel() is exercised."""

    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class SelfDestructScopeTests(unittest.TestCase):
    """Clearing one chat must not drop another chat's pending deletes."""

    def tearDown(self):
        telegram_bot._pending_deletes.clear()

    def test_cancelling_one_chat_leaves_the_other(self):
        sb, vcs = _FakeTimer(), _FakeTimer()
        telegram_bot._pending_deletes[("-100111", 1)] = sb
        telegram_bot._pending_deletes[("-100222", 2)] = vcs

        self.assertEqual(telegram_bot.cancel_all_self_destructs("-100111"), 1)

        self.assertTrue(sb.cancelled)
        self.assertFalse(vcs.cancelled)
        self.assertNotIn(("-100111", 1), telegram_bot._pending_deletes)
        self.assertIn(("-100222", 2), telegram_bot._pending_deletes)

    def test_an_int_chat_id_matches_the_string_key(self):
        timer = _FakeTimer()
        telegram_bot._pending_deletes[("-100111", 1)] = timer
        self.assertEqual(telegram_bot.cancel_all_self_destructs(-100111), 1)
        self.assertTrue(timer.cancelled)

    def test_no_argument_still_cancels_everything(self):
        for index, chat in enumerate(("-100111", "-100222")):
            telegram_bot._pending_deletes[(chat, index)] = _FakeTimer()
        self.assertEqual(telegram_bot.cancel_all_self_destructs(), 2)
        self.assertEqual(telegram_bot._pending_deletes, {})


class SuppressedAlertTests(unittest.TestCase):
    """A suppressed alert must stay pending, not be recorded as delivered."""

    def test_send_spaced_reports_whether_it_sent(self):
        with patch.object(telegram_service, "group_clear_running", lambda _g=None: True):
            with patch.object(telegram_service, "send_message", new_callable=AsyncMock) as send:
                self.assertFalse(_run(alert_service._send_spaced("x", 0, "sb")))
        send.assert_not_awaited()

        with patch.object(telegram_service, "group_clear_running", lambda _g=None: False):
            with patch.object(telegram_service, "send_message", new_callable=AsyncMock):
                self.assertTrue(_run(alert_service._send_spaced("x", 0, "sb")))

    def _run_check(self, accounts, clear_running):
        class _Repo:
            def __init__(self, _s):
                pass

            async def list_all(self):
                return accounts

        class _Session:
            async def commit(self):
                return None

            async def refresh(self, _o):
                return None

        config = {
            "enabled": True,
            "thresholds": [75, 95],
            "rearm_margin": 5.0,
            "overspend_buffer_usd": 250.0,
        }
        with patch.object(alert_service, "AccountRepository", _Repo), patch.object(
            alert_service, "get_alert_config", AsyncMock(return_value=config)
        ), patch.object(
            alert_service.telegram_service, "is_configured", lambda group=None: True
        ), patch.object(
            alert_service.telegram_service, "group_clear_running", lambda _g=None: clear_running
        ), patch.object(
            alert_service.telegram_service, "send_message", new_callable=AsyncMock
        ), patch(
            "app.services.new_api_service.gateway_still_live", lambda _a: True
        ):
            return _run(alert_service.check_new_api_credit_alerts(_Session()))

    def test_alert_level_is_not_advanced_when_the_send_was_suppressed(self):
        account = _account("Lioxi-Gaurav1", "sb", 7600)
        summary = self._run_check([account], clear_running=True)
        # Nothing was delivered, so the 75% warning must still be owed.
        self.assertEqual(summary["sent"], 0)
        self.assertEqual(account.new_api_alert_level, 0)

    def test_alert_level_advances_on_a_real_send(self):
        account = _account("Lioxi-Gaurav1", "sb", 7600)
        summary = self._run_check([account], clear_running=False)
        self.assertEqual(summary["sent"], 1)
        self.assertEqual(account.new_api_alert_level, 75)


class SendTargetValidationTests(unittest.TestCase):
    def test_unknown_target_is_rejected_not_defaulted_to_sb(self):
        from fastapi import HTTPException

        from app.routers.alerts import _targets

        for bad in ("vc", "v2", "", "   ", "sbb", None):
            with self.assertRaises(HTTPException, msg=bad):
                _targets(bad)

    def test_known_targets_resolve(self):
        from app.routers.alerts import _targets

        self.assertEqual(_targets("sb"), ["sb"])
        self.assertEqual(_targets(" VCS "), ["vcs"])
        self.assertEqual(sorted(_targets("both")), ["sb", "vcs"])


if __name__ == "__main__":
    unittest.main()
