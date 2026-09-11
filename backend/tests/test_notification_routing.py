"""Alerts and notices must reach only their own group's chat.

Trade Center (SB) and Trade_Center_V2 (VCS) are separate audiences. A leak in
either direction shows one group another group's spend, so every send path is
pinned here.
"""

import asyncio
import re
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services import telegram_service
from app.services.telegram_service import TelegramError, group_chat_id, group_for_chat, is_configured

SB_CHAT = "-100111"
VCS_CHAT = "-100222"


def _settings(sb=SB_CHAT, vcs=VCS_CHAT, token="token"):
    return SimpleNamespace(
        telegram_bot_token=token,
        telegram_chat_id=sb,
        telegram_vcs_chat_id=vcs,
        telegram_admin_id_set={"111"},
        telegram_owner_id_set={"111"},
    )


def _run(coro):
    return asyncio.run(coro)


_ALERT_CONFIG = {
    "enabled": True,
    "thresholds": [75, 95],
    "rearm_margin": 5.0,
    "overspend_buffer_usd": 250.0,
}


def _account(name, group, spend, limit=10000):
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
        new_api_alert_level=0,
        credits_limit=limit,
        credits_currency="USD",
        credits_limit_manual=False,
    )


class ChatResolutionTests(unittest.TestCase):
    def test_each_group_resolves_to_its_own_chat(self):
        with patch("app.services.telegram_service.get_settings", return_value=_settings()):
            self.assertEqual(group_chat_id("sb"), SB_CHAT)
            self.assertEqual(group_chat_id("vcs"), VCS_CHAT)
            self.assertEqual(group_chat_id(None), SB_CHAT)

    def test_unknown_group_falls_back_to_sb_not_vcs(self):
        with patch("app.services.telegram_service.get_settings", return_value=_settings()):
            self.assertEqual(group_chat_id("nonsense"), SB_CHAT)

    def test_reverse_lookup(self):
        with patch("app.services.telegram_service.get_settings", return_value=_settings()):
            self.assertEqual(group_for_chat(SB_CHAT), "sb")
            self.assertEqual(group_for_chat(VCS_CHAT), "vcs")
            self.assertIsNone(group_for_chat("-100999"))

    def test_unconfigured_vcs_never_borrows_the_sb_chat(self):
        # The critical safety property: if VCS has no chat id, VCS messages
        # must not silently land in the SB group.
        with patch("app.services.telegram_service.get_settings", return_value=_settings(vcs="")):
            self.assertEqual(group_chat_id("vcs"), "")
            self.assertFalse(is_configured("vcs"))
            self.assertTrue(is_configured("sb"))
            with self.assertRaises(TelegramError):
                _run(telegram_service.send_message("x", group="vcs"))


class SendRoutingTests(unittest.TestCase):
    def _capture(self, **kwargs):
        seen = {}

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

            async def post(self, _url, json=None, **_kw):
                seen.update(json or {})
                import httpx

                return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

        with patch("app.services.telegram_service.get_settings", return_value=_settings()):
            with patch("app.services.telegram_service.httpx.AsyncClient", return_value=FakeClient()):
                _run(telegram_service.send_message("hello", **kwargs))
        return seen

    def test_sb_message_goes_to_sb_chat(self):
        self.assertEqual(str(self._capture(group="sb")["chat_id"]), SB_CHAT)

    def test_vcs_message_goes_to_vcs_chat(self):
        self.assertEqual(str(self._capture(group="vcs")["chat_id"]), VCS_CHAT)

    def test_default_is_sb(self):
        self.assertEqual(str(self._capture()["chat_id"]), SB_CHAT)

    def test_explicit_chat_id_still_wins(self):
        self.assertEqual(str(self._capture(chat_id="-100999")["chat_id"]), "-100999")


class DeployNoticeRoutingTests(unittest.TestCase):
    def test_notice_follows_the_accounts_group(self):
        for group, expected in (("sb", SB_CHAT), ("vcs", VCS_CHAT)):
            with patch("app.services.telegram_service.get_settings", return_value=_settings()):
                with patch(
                    "app.services.telegram_service.send_message", new_callable=AsyncMock
                ) as send:
                    _run(telegram_service._notify_k3_deployed("Ambarish", "Ambarish", "admin", group))
            self.assertEqual(send.await_args.kwargs.get("group"), group, group)

    def test_notice_is_skipped_when_that_group_has_no_chat(self):
        with patch("app.services.telegram_service.get_settings", return_value=_settings(vcs="")):
            with patch("app.services.telegram_service.send_message", new_callable=AsyncMock) as send:
                _run(telegram_service._notify_k3_deployed("Ambarish", "Ambarish", "admin", "vcs"))
        send.assert_not_awaited()


class ClearJobIsolationTests(unittest.TestCase):
    def tearDown(self):
        telegram_service._clear_jobs.clear()
        telegram_service._clear_tasks.clear()
        telegram_service._group_high_water.clear()

    def test_clear_running_is_per_group(self):
        telegram_service._clear_jobs["sb"] = {"running": True}
        self.assertTrue(telegram_service.group_clear_running("sb"))
        self.assertFalse(telegram_service.group_clear_running("vcs"))

    def test_high_water_marks_do_not_share(self):
        telegram_service.note_group_message_id = telegram_service.note_group_message_id
        with patch("app.services.telegram_service.get_settings", return_value=_settings()):
            telegram_service.note_group_message_id(SB_CHAT, 500)
            telegram_service.note_group_message_id(VCS_CHAT, 7)
        self.assertEqual(telegram_service._group_high_water.get("sb"), 500)
        self.assertEqual(telegram_service._group_high_water.get("vcs"), 7)

    def test_unrelated_chat_is_not_tracked(self):
        with patch("app.services.telegram_service.get_settings", return_value=_settings()):
            telegram_service.note_group_message_id("-100999", 42)
        self.assertEqual(telegram_service._group_high_water, {})


class AlertLoopRoutingTests(unittest.TestCase):
    """check_new_api_credit_alerts must post each account to its own chat."""

    def test_threshold_alerts_split_by_group(self):
        from app.services import alert_service

        sent: list[tuple[str, str]] = []

        async def fake_send(text, sent_so_far, group=None):
            sent.append((group, text))
            return True

        sb = _account("Lioxi-Gaurav1", "sb", 7600)
        vcs = _account("Ambarish", "vcs", 7700)

        class _Repo:
            def __init__(self, _s):
                pass

            async def list_all(self):
                return [sb, vcs]

        class _Session:
            async def commit(self):
                return None

            async def refresh(self, _o):
                return None

        with patch.object(alert_service, "_send_spaced", fake_send), patch.object(
            alert_service, "AccountRepository", _Repo
        ), patch.object(
            alert_service, "get_alert_config", AsyncMock(return_value=_ALERT_CONFIG)
        ), patch.object(
            alert_service.telegram_service, "is_configured", lambda group=None: True
        ), patch(
            "app.services.new_api_service.gateway_still_live", lambda _a: True
        ):
            _run(alert_service.check_new_api_credit_alerts(_Session()))

        by_group = {g: t for g, t in sent}
        self.assertIn("sb", by_group)
        self.assertIn("vcs", by_group)
        # Neither group's message mentions the other's account.
        self.assertIn("Lioxi-Gaurav1", by_group["sb"])
        self.assertNotIn("Ambarish", by_group["sb"])
        self.assertIn("Ambarish", by_group["vcs"])
        self.assertNotIn("Lioxi-Gaurav1", by_group["vcs"])

    def test_vcs_silent_when_only_sb_is_configured(self):
        from app.services import alert_service

        sent: list[tuple[str, str]] = []

        async def fake_send(text, sent_so_far, group=None):
            sent.append((group, text))
            return True

        class _Repo:
            def __init__(self, _s):
                pass

            async def list_all(self):
                return [_account("Lioxi-Gaurav1", "sb", 7600), _account("Ambarish", "vcs", 7700)]

        class _Session:
            async def commit(self):
                return None

            async def refresh(self, _o):
                return None

        with patch.object(alert_service, "_send_spaced", fake_send), patch.object(
            alert_service, "AccountRepository", _Repo
        ), patch.object(
            alert_service, "get_alert_config", AsyncMock(return_value=_ALERT_CONFIG)
        ), patch.object(
            alert_service.telegram_service, "is_configured", lambda group=None: group != "vcs"
        ), patch(
            "app.services.new_api_service.gateway_still_live", lambda _a: True
        ):
            _run(alert_service.check_new_api_credit_alerts(_Session()))

        groups = {g for g, _ in sent}
        self.assertEqual(groups, {"sb"})


class MessageWordingTests(unittest.TestCase):
    """Recipients must never see the routing. The group decides WHERE a message
    goes, never WHAT it says — no "SB"/"VCS"/"group" wording in any chat text."""

    # Word-boundary matched, so a person named e.g. "Sbhat" cannot trip this.
    BANNED = (r"\bsb\b", r"\bvcs\b", r"join group", r"group_tag")

    def _assert_clean(self, text, label):
        lowered = text.lower()
        for pattern in self.BANNED:
            self.assertIsNone(
                re.search(pattern, lowered),
                f"{label} leaks routing wording {pattern!r}: {text!r}",
            )

    def test_the_guard_itself_catches_a_leak(self):
        # Guards against this suite silently passing if _assert_clean broke.
        with self.assertRaises(AssertionError):
            self._assert_clean("Spend hit the stop point (VCS)", "sanity")
        with self.assertRaises(AssertionError):
            self._assert_clean("sent to SB group", "sanity")
        # ...while a legitimate name that merely contains the letters is fine.
        self._assert_clean("Sbhat · lioxi-vcsomething1", "sanity")

    def test_deploy_notice_has_no_group_wording(self):
        from app.services.telegram_service import format_k3_deployed_notice

        for person, account in (("Ambarish", "Ambarish"), ("Gaurav", "Lioxi-Gaurav1")):
            self._assert_clean(format_k3_deployed_notice(person, account, "admin"), "k3 notice")

    def test_alert_cards_have_no_group_wording(self):
        from app.services.alert_service import _format_exhausted_alert, _format_threshold_alert

        for group in ("sb", "vcs"):
            account = _account("Ambarish" if group == "vcs" else "Lioxi-Gaurav1", group, 7600)
            self._assert_clean(_format_threshold_alert(account, 75, 76.0), "threshold alert")
            self._assert_clean(
                _format_exhausted_alert(account, {"O1": 1}, None, False, 250.0), "exhausted alert"
            )

    def test_bot_help_has_no_group_wording(self):
        from app.services.telegram_bot import _HELP, _PUBLIC_HELP

        self._assert_clean(_PUBLIC_HELP, "public help")
        self._assert_clean(_HELP, "admin help")

    def test_live_card_has_no_group_wording(self):
        from app.services.telegram_bot import _live_card

        self._assert_clean(_live_card("Ambarish", [_account("Ambarish", "vcs", 100)]), "live card")

    def test_live_empty_and_no_match_replies_are_clean(self):
        from app.services.telegram_bot import _live_ttl_note

        self._assert_clean(f"No live channels to show right now.{_live_ttl_note()}", "empty live")
        self._assert_clean(f"No person matching x.{_live_ttl_note()}", "no match")


if __name__ == "__main__":
    unittest.main()
