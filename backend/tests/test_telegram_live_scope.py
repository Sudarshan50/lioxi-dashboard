"""/live is public in the group chat, so it must only ever expose SB."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.telegram_bot import (
    _cmd_live,
    _group_by_token,
    _live_groups,
    _person_token,
    group_scope,
)


def _acct(name, owner, group="sb", live=True, spend=100.0):
    return SimpleNamespace(
        name=name,
        owner_tag=owner,
        group_tag=group,
        new_api_name=name,
        new_api_gateway="O1",
        new_api_cost_usd=spend,
        new_api_cost_o1_usd=spend,
        new_api_cost_o2_usd=0,
        new_api_status=1 if live else 2,
        credits_limit=10000,
        credits_currency="USD",
    )


FLEET = [
    _acct("Lioxi-Gaurav1", "Gaurav"),
    _acct("Lioxi-Arif1", "Arif"),
    _acct("Lioxi-Paused", "Dhruv", live=False),
    _acct("Ambarish", "Ambarish", group="vcs"),
    _acct("Tanish", "Tanish", group="vcs"),
    _acct("VcsPaused", "Vinay", group="vcs", live=False),
]


def _run(coro):
    return asyncio.run(coro)


def _patched_cmd_live(query="", accounts=FLEET, scope="sb"):
    class _Repo:
        def __init__(self, _session):
            pass

        async def list_all(self):
            return list(accounts)

    class _Session:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *exc):
            return False

    with patch("app.services.telegram_bot.AccountRepository", _Repo), patch(
        "app.services.telegram_bot.SessionLocal", _Session
    ):
        return _run(_cmd_live(query, scope=scope))


class LiveScopeTests(unittest.TestCase):
    def test_live_groups_exclude_vcs(self):
        tags = [tag for tag, _ in _live_groups(FLEET)]
        self.assertEqual(tags, ["Arif", "Gaurav"])
        self.assertNotIn("Ambarish", tags)
        self.assertNotIn("Tanish", tags)

    def test_paused_sb_still_excluded(self):
        self.assertNotIn("Dhruv", [tag for tag, _ in _live_groups(FLEET)])

    def test_missing_group_tag_counts_as_sb(self):
        legacy = SimpleNamespace(
            name="Old", owner_tag="Snig", new_api_name="Old", new_api_gateway="O1",
            new_api_cost_usd=1.0, new_api_cost_o1_usd=1.0, new_api_cost_o2_usd=0,
            new_api_status=1, credits_limit=10000, credits_currency="USD",
        )
        self.assertEqual([tag for tag, _ in _live_groups([legacy])], ["Snig"])

    def test_picker_lists_only_sb(self):
        text, keyboard = _patched_cmd_live()
        self.assertIn("Whose live usage?", text)
        buttons = str(keyboard)
        self.assertIn("Gaurav", buttons)
        self.assertNotIn("Ambarish", buttons)
        self.assertNotIn("Tanish", buttons)

    def test_querying_a_vcs_name_finds_nothing(self):
        for name in ("Ambarish", "tanish", "Vinay"):
            text, keyboard = _patched_cmd_live(name)
            self.assertIn("No person matching", text, name)
            self.assertIsNone(keyboard)

    def test_querying_an_sb_name_still_works(self):
        text, keyboard = _patched_cmd_live("Gaurav")
        self.assertIsNone(keyboard)
        self.assertIn("Gaurav", text)

    def test_vcs_token_is_not_resolvable_through_the_callback(self):
        # The button callback re-derives groups from _live_groups, so a token
        # for a VCS person cannot be replayed to reveal their usage.
        groups = _live_groups(FLEET)
        self.assertIsNone(_group_by_token(groups, _person_token("Ambarish")))
        self.assertIsNotNone(_group_by_token(groups, _person_token("Gaurav")))

    def test_all_vcs_fleet_shows_nothing(self):
        text, keyboard = _patched_cmd_live("", [a for a in FLEET if a.group_tag == "vcs"])
        self.assertIn("No live channels", text)
        self.assertIsNone(keyboard)


class VcsScopeTests(unittest.TestCase):
    """The second group chat is the mirror image: VCS only."""

    def test_vcs_scope_lists_only_vcs(self):
        tags = [tag for tag, _ in _live_groups(FLEET, "vcs")]
        self.assertEqual(tags, ["Ambarish", "Tanish"])
        self.assertNotIn("Gaurav", tags)

    def test_vcs_paused_still_excluded(self):
        self.assertNotIn("Vinay", [tag for tag, _ in _live_groups(FLEET, "vcs")])

    def test_vcs_picker_hides_sb(self):
        text, keyboard = _patched_cmd_live(scope="vcs")
        buttons = str(keyboard)
        self.assertIn("Ambarish", buttons)
        self.assertNotIn("Gaurav", buttons)
        self.assertNotIn("Arif", buttons)

    def test_sb_name_not_findable_from_the_vcs_group(self):
        text, keyboard = _patched_cmd_live("Gaurav", scope="vcs")
        self.assertIn("No person matching", text)

    def test_vcs_name_not_findable_from_the_sb_group(self):
        text, keyboard = _patched_cmd_live("Ambarish", scope="sb")
        self.assertIn("No person matching", text)

    def test_admin_private_chat_sees_both(self):
        tags = [tag for tag, _ in _live_groups(FLEET, None)]
        self.assertIn("Gaurav", tags)
        self.assertIn("Ambarish", tags)

    def test_cross_group_token_replay_is_blocked(self):
        sb_groups = _live_groups(FLEET, "sb")
        vcs_groups = _live_groups(FLEET, "vcs")
        # A button token minted in one group resolves to nothing in the other.
        self.assertIsNone(_group_by_token(vcs_groups, _person_token("Gaurav")))
        self.assertIsNone(_group_by_token(sb_groups, _person_token("Ambarish")))


class GroupScopeRoutingTests(unittest.TestCase):
    SB, VCS = "-100111", "-100222"

    def _chat(self, chat_id, kind="supergroup"):
        return {"id": chat_id, "type": kind}

    def test_each_group_maps_to_its_own_scope(self):
        self.assertEqual(group_scope(self._chat(self.SB), self.SB, self.VCS), "sb")
        self.assertEqual(group_scope(self._chat(self.VCS), self.SB, self.VCS), "vcs")

    def test_unknown_group_is_closed(self):
        self.assertIsNone(group_scope(self._chat("-100999"), self.SB, self.VCS))

    def test_private_chat_is_not_a_group_scope(self):
        self.assertIsNone(group_scope(self._chat(self.SB, "private"), self.SB, self.VCS))

    def test_unconfigured_vcs_group_stays_closed(self):
        # Blank config must not make every chat match the empty string.
        self.assertIsNone(group_scope(self._chat(""), self.SB, ""))
        self.assertIsNone(group_scope(self._chat(self.VCS), self.SB, ""))


class ScopeWiringTests(unittest.IsolatedAsyncioTestCase):
    """The chat -> scope hand-off is the actual leak guard.

    _live_groups being correct is worthless if the dispatcher forgets to pass
    the chat's scope, so pin both entry points.
    """

    SB, VCS = "-100111", "-100222"

    def _settings(self):
        return SimpleNamespace(
            telegram_bot_token="token",
            telegram_chat_id=self.SB,
            telegram_vcs_chat_id=self.VCS,
            telegram_admin_id_set={"111"},
            telegram_owner_id_set={"111"},
        )

    def _update(self, chat_id, kind="supergroup", sender="333"):
        return {
            "update_id": 1,
            "message": {
                "message_id": 9,
                "from": {"id": sender},
                "chat": {"id": chat_id, "type": kind},
                "text": "/live",
            },
        }

    async def _scope_for(self, chat_id, kind="supergroup", sender="333"):
        from app.services import telegram_bot

        seen = {}

        async def fake_cmd_live(query, source_message_id=None, scope="sb"):
            seen["scope"] = scope
            return "ok", None

        with patch("app.services.telegram_bot.get_settings", return_value=self._settings()):
            with patch("app.services.telegram_bot._cmd_live", fake_cmd_live):
                with patch(
                    "app.services.telegram_bot.telegram_service.send_message",
                    new_callable=AsyncMock,
                    return_value=5,
                ):
                    await telegram_bot._process_update(self._update(chat_id, kind, sender))
        return seen.get("scope", "<never called>")

    async def test_sb_group_command_gets_sb_scope(self):
        self.assertEqual(await self._scope_for(self.SB), "sb")

    async def test_vcs_group_command_gets_vcs_scope(self):
        self.assertEqual(await self._scope_for(self.VCS), "vcs")

    async def test_admin_private_chat_gets_every_group(self):
        self.assertIsNone(await self._scope_for("111", kind="private", sender="111"))

    async def test_unknown_group_is_ignored_entirely(self):
        self.assertEqual(await self._scope_for("-100999"), "<never called>")

    async def test_callback_resolves_names_in_the_chats_own_scope(self):
        from app.services import telegram_bot

        seen = {}

        def fake_live_groups(accounts, scope=None):
            seen["scope"] = scope
            return []

        callback = {
            "id": "cb1",
            "from": {"id": "333"},
            "data": f"live:{_person_token('Ambarish')}",
            "message": {"message_id": 4, "chat": {"id": self.VCS, "type": "supergroup"}},
        }

        class _Repo:
            def __init__(self, _s):
                pass

            async def list_all(self):
                return []

        class _Session:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, *_exc):
                return False

        with patch("app.services.telegram_bot.get_settings", return_value=self._settings()):
            with patch("app.services.telegram_bot._live_groups", fake_live_groups):
                with patch("app.services.telegram_bot.AccountRepository", _Repo):
                    with patch("app.services.telegram_bot.SessionLocal", _Session):
                        with patch(
                            "app.services.telegram_bot.telegram_service.answer_callback_query",
                            new_callable=AsyncMock,
                        ):
                            with patch(
                                "app.services.telegram_bot._replace_message",
                                new_callable=AsyncMock,
                            ):
                                await telegram_bot._process_callback(callback)
        self.assertEqual(seen.get("scope"), "vcs")


class LivePickerOwnerTests(unittest.IsolatedAsyncioTestCase):
    SB = "-100111"

    def setUp(self):
        from app.services import telegram_bot

        telegram_bot._picker_owners.clear()

    def tearDown(self):
        from app.services import telegram_bot

        telegram_bot._picker_owners.clear()

    def _settings(self):
        return SimpleNamespace(
            telegram_bot_token="token",
            telegram_chat_id=self.SB,
            telegram_vcs_chat_id="-100222",
            telegram_admin_id_set={"111"},
            telegram_owner_id_set={"111"},
        )

    def _callback(self, sender, data="live:abc"):
        return {
            "id": f"cb-{sender}",
            "from": {"id": sender},
            "data": data,
            "message": {"message_id": 4, "chat": {"id": self.SB, "type": "supergroup"}},
        }

    def _update(self, sender="333"):
        return {
            "update_id": 1,
            "message": {
                "message_id": 9,
                "from": {"id": sender},
                "chat": {"id": self.SB, "type": "supergroup"},
                "text": "/live",
            },
        }

    async def test_picker_is_owned_by_the_live_requester(self):
        from app.services import telegram_bot

        async def fake_cmd_live(query, source_message_id=None, scope="sb"):
            return "Whose live usage?", {"inline_keyboard": [[{"text": "Gaurav", "callback_data": "live:x"}]]}

        with patch("app.services.telegram_bot.get_settings", return_value=self._settings()):
            with patch("app.services.telegram_bot._cmd_live", fake_cmd_live):
                with patch(
                    "app.services.telegram_bot.telegram_service.send_message",
                    new_callable=AsyncMock,
                    return_value=5,
                ):
                    with patch("app.services.telegram_bot.schedule_self_destruct", return_value=None):
                        await telegram_bot._process_update(self._update("333"))
        self.assertEqual(telegram_bot._picker_owner(self.SB, 5), "333")

    async def _click(self, sender, data="live:abc"):
        from app.services import telegram_bot

        answers = []
        replaced = []

        async def fake_answer(callback_id):
            answers.append(callback_id)

        async def fake_replace(*_args, **_kwargs):
            replaced.append(True)

        class _Repo:
            def __init__(self, _s):
                pass

            async def list_all(self):
                return []

        class _Session:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, *_exc):
                return False

        telegram_bot._remember_picker_owner(self.SB, 4, "333")
        with patch("app.services.telegram_bot.get_settings", return_value=self._settings()):
            with patch("app.services.telegram_bot.AccountRepository", _Repo):
                with patch("app.services.telegram_bot.SessionLocal", _Session):
                    with patch(
                        "app.services.telegram_bot.telegram_service.answer_callback_query",
                        side_effect=fake_answer,
                    ):
                        with patch("app.services.telegram_bot._replace_message", side_effect=fake_replace):
                            with patch(
                                "app.services.telegram_bot.telegram_service.edit_message_text",
                                new_callable=AsyncMock,
                            ) as edit:
                                with patch(
                                    "app.services.telegram_bot.telegram_service.send_message",
                                    new_callable=AsyncMock,
                                ) as send:
                                    await telegram_bot._process_callback(self._callback(sender, data))
        return answers, replaced, edit, send

    async def test_requester_click_still_edits_the_picker(self):
        answers, replaced, _edit, send = await self._click("333")
        self.assertEqual(replaced, [True])
        self.assertEqual(len(answers), 1)
        send.assert_not_awaited()

    async def test_other_member_cannot_use_the_picker(self):
        answers, replaced, edit, send = await self._click("444")
        self.assertEqual(len(answers), 1)
        self.assertEqual(replaced, [])
        edit.assert_not_awaited()
        send.assert_not_awaited()

    async def test_other_member_cannot_page_or_cancel(self):
        for data in ("livep:1", "cancel:9"):
            answers, replaced, edit, send = await self._click("444", data)
            self.assertEqual(len(answers), 1, data)
            self.assertEqual(replaced, [], data)
            edit.assert_not_awaited()
            send.assert_not_awaited()


class LiveGifPreludeTests(unittest.IsolatedAsyncioTestCase):
    SB, VCS = "-100111", "-100222"
    GIF_USER = "7094346806"

    def _settings(self):
        return SimpleNamespace(
            telegram_bot_token="token",
            telegram_chat_id=self.SB,
            telegram_vcs_chat_id=self.VCS,
            telegram_admin_id_set={"111"},
            telegram_owner_id_set={"111"},
        )

    def _update(self, chat_id, sender, kind="supergroup", username=None, text="/live"):
        user = {"id": sender}
        if username:
            user["username"] = username
        return {
            "update_id": 1,
            "message": {
                "message_id": 9,
                "from": user,
                "chat": {"id": chat_id, "type": kind},
                "text": text,
            },
        }

    async def _run(self, update, gif_path="/tmp/animation.gif.mp4"):
        from pathlib import Path

        from app.services import telegram_bot

        order = []
        scheduled = []

        async def fake_cmd_live(query, source_message_id=None, scope="sb"):
            return "live-card", None

        async def fake_gif(*_args, **_kwargs):
            order.append("gif")
            return 77

        async def fake_send(*_args, **_kwargs):
            order.append("live")
            return 5

        def fake_sched(chat_id, *message_ids, delay=telegram_bot.LIVE_MESSAGE_TTL_SECONDS):
            scheduled.append((str(chat_id), tuple(message_ids), delay))
            return None

        with patch("app.services.telegram_bot.get_settings", return_value=self._settings()):
            with patch("app.services.telegram_bot._cmd_live", fake_cmd_live):
                with patch("app.services.telegram_bot._live_gif_path", return_value=Path(gif_path)):
                    with patch(
                        "app.services.telegram_bot.telegram_service.send_animation",
                        side_effect=fake_gif,
                    ):
                        with patch(
                            "app.services.telegram_bot.telegram_service.send_message",
                            side_effect=fake_send,
                        ):
                            with patch("app.services.telegram_bot.schedule_self_destruct", side_effect=fake_sched):
                                await telegram_bot._process_update(update)
        return order, scheduled

    async def test_trade_center_gif_user_gets_gif_then_live(self):
        from app.services.telegram_bot import LIVE_GIF_TTL_SECONDS, LIVE_MESSAGE_TTL_SECONDS

        order, scheduled = await self._run(self._update(self.SB, self.GIF_USER))
        self.assertEqual(order, ["gif", "live"])
        self.assertEqual(scheduled[0], (self.SB, (77,), LIVE_GIF_TTL_SECONDS))
        self.assertEqual(scheduled[1][0], self.SB)
        self.assertIn(5, scheduled[1][1])
        self.assertEqual(scheduled[1][2], LIVE_MESSAGE_TTL_SECONDS)

    async def test_username_alone_does_not_trigger(self):
        order, _scheduled = await self._run(
            self._update(self.SB, "999", username="abhishek_iitd2")
        )
        self.assertEqual(order, ["live"])

    async def test_other_trade_center_member_skips_gif(self):
        order, scheduled = await self._run(self._update(self.SB, "333"))
        self.assertEqual(order, ["live"])
        self.assertTrue(all(delay == 30 for *_rest, delay in scheduled))

    async def test_same_user_in_vcs_group_skips_gif(self):
        order, _scheduled = await self._run(self._update(self.VCS, self.GIF_USER))
        self.assertEqual(order, ["live"])

    async def test_missing_gif_file_still_sends_live(self):
        from app.services import telegram_bot

        called = []

        async def fake_cmd_live(query, source_message_id=None, scope="sb"):
            return "live-card", None

        async def fake_send(*_args, **_kwargs):
            called.append("live")
            return 5

        with patch("app.services.telegram_bot.get_settings", return_value=self._settings()):
            with patch("app.services.telegram_bot._cmd_live", fake_cmd_live):
                with patch("app.services.telegram_bot._live_gif_path", return_value=None):
                    with patch(
                        "app.services.telegram_bot.telegram_service.send_animation",
                        new_callable=AsyncMock,
                    ) as gif:
                        with patch(
                            "app.services.telegram_bot.telegram_service.send_message",
                            side_effect=fake_send,
                        ):
                            with patch("app.services.telegram_bot.schedule_self_destruct", return_value=None):
                                await telegram_bot._process_update(self._update(self.SB, self.GIF_USER))
        gif.assert_not_awaited()
        self.assertEqual(called, ["live"])

    def test_is_live_gif_user_matches_numeric_id_only(self):
        from app.services.telegram_bot import _is_live_gif_user

        self.assertTrue(_is_live_gif_user({"id": 7094346806}))
        self.assertTrue(_is_live_gif_user({"id": "7094346806"}))
        self.assertFalse(_is_live_gif_user({"id": "1", "username": "abhishek_iitd2"}))
        self.assertFalse(_is_live_gif_user({"id": "333"}))
        self.assertFalse(_is_live_gif_user({}))


if __name__ == "__main__":
    unittest.main()
