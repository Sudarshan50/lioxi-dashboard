import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from app.services.telegram_bot import (
    LIVE_MESSAGE_TTL_SECONDS,
    PROJECTED_INCOME_RATE,
    UNTAGGED_PERSON,
    _account_card,
    _channel_report,
    _cmd_test,
    _live_card,
    _live_groups,
    _people_groups,
    _person_card,
    _projected,
    cancel_all_self_destructs,
    chat_is_allowed,
    schedule_self_destruct,
)
from app.services import telegram_service
from app.services.telegram_service import TelegramError, clear_chat, clear_group_snapshot, start_clear_group_chat


def _acct(name, owner, spend, live=True, channel=None, grant=10000, gateway="O1"):
    return SimpleNamespace(
        name=name,
        owner_tag=owner,
        new_api_name=channel or name,
        new_api_gateway=gateway,
        new_api_cost_usd=spend,
        new_api_cost_o1_usd=spend,
        new_api_cost_o2_usd=0,
        new_api_status=1 if live else 2,
        credits_limit=grant,
        credits_currency="USD",
    )


class PeopleUsage(unittest.TestCase):
    def test_projected_income_is_send_times_rate(self):
        self.assertEqual(_projected(100), 12.0)
        self.assertEqual(_projected(100), round(100 * PROJECTED_INCOME_RATE, 2))

    def test_groups_by_person_and_puts_paused_last(self):
        accounts = [
            _acct("paused-arif", "Arif", 80, live=False),
            _acct("live-gaurav", "Gaurav", 50),
            _acct("paused-gaurav", "Gaurav", 10, live=False),
            _acct("orphan", None, 5),
        ]
        groups = _people_groups(accounts)
        self.assertEqual([tag for tag, _ in groups], ["Gaurav", "Arif", UNTAGGED_PERSON])
        gaurav = groups[0][1]
        self.assertEqual([row.name for row in gaurav], ["live-gaurav", "paused-gaurav"])

    def test_person_card_is_channelwise(self):
        text = _person_card(
            "Gaurav",
            [
                _acct("Old", "Gaurav", 20, live=False, channel="kimi-old"),
                _acct("Gaurav1", "Gaurav", 3180.53, channel="kimi-k3-500k-proxy-20"),
            ],
        )
        self.assertIn("Projected income: <b>$384.06</b>", text)
        self.assertIn("<b>1.</b>", text)
        self.assertIn("<b>2.</b>", text)
        self.assertIn("<code>kimi-k3-500k-proxy-20</code>", text)
        self.assertIn("<code>kimi-old</code>", text)
        self.assertIn("O1 spend: $3,180.53", text)
        self.assertIn("Azure grant: $10,000.00", text)
        self.assertIn("paused", text)
        self.assertLess(text.index("kimi-k3-500k-proxy-20"), text.index("kimi-old"))

    def test_legacy_usage_card_matches_people_format(self):
        text = _account_card(_acct("Gaurav1", "Gaurav", 3180.53, channel="kimi-k3-500k-proxy-20"))
        self.assertNotIn("Projected income", text)
        self.assertIn("Gaurav · Gaurav1", text)
        self.assertNotIn("<b>1.</b>", text)
        self.assertIn("<code>kimi-k3-500k-proxy-20</code>", text)
        self.assertIn("O1 spend: $3,180.53", text)
        self.assertIn("Azure grant: $10,000.00", text)
        self.assertNotIn("Gateway", text)
        self.assertNotIn("Synced", text)

    def test_alerts_report_uses_same_cards(self):
        hot = _acct("Gaurav1", "Gaurav", 8000, channel="kimi-hot")
        text = _channel_report([hot], "⚠ <b>At or above 75%</b> · 1 channel", numbered=False)
        self.assertNotIn("Projected income", text)
        self.assertIn("At or above 75%", text)
        self.assertNotIn("<b>1.</b>", text)
        self.assertIn("<code>kimi-hot</code>", text)
        self.assertIn("O1 spend: $8,000.00", text)

    def test_sample_command_shows_usage_and_alerts(self):
        text = _cmd_test()
        self.assertIn("Example — /usage", text)
        self.assertIn("Example — /alerts", text)
        self.assertIn("Example — auto-disable", text)
        self.assertIn("kimi-k3-500k-proxy-20", text)
        self.assertIn("kimi-k3-500k-proxy-21", text)
        self.assertIn("kimi-k3-500k-proxy-22", text)
        self.assertNotIn("Projected income", text)
        self.assertIn("At or above 75%", text)
        self.assertIn("Spend hit the stop point", text)
        self.assertIn("Auto-disabled", text)
        self.assertIn("O1 spend: $3,180.53", text)
        self.assertIn("O1 spend: $8,000.00", text)

    def test_private_chat_allows_every_admin(self):
        admins = {"111", "222"}
        kwargs = {"admin_ids": admins, "owner_id": {"111", "222"}, "group_id": "-100"}
        self.assertTrue(chat_is_allowed({"type": "private", "id": 111}, "111", **kwargs))
        self.assertTrue(chat_is_allowed({"type": "private", "id": 222}, "222", **kwargs))
        self.assertFalse(chat_is_allowed({"type": "private", "id": 333}, "333", **kwargs))
        self.assertFalse(chat_is_allowed({"type": "supergroup", "id": -100}, "222", **kwargs))
        self.assertFalse(chat_is_allowed({"type": "supergroup", "id": -999}, "111", **kwargs))

    def test_live_is_group_only_for_non_admins(self):
        from app.services.telegram_bot import _callback_allowed, _command_allowed

        settings = SimpleNamespace(
            telegram_admin_id_set={"111"},
            telegram_owner_id_set={"111"},
            telegram_chat_id="-100",
            telegram_vcs_chat_id="-200",
        )
        with patch("app.services.telegram_bot.get_settings", return_value=settings):
            dm = {"type": "private", "id": 333}
            admin_dm = {"type": "private", "id": 111}
            group = {"type": "supergroup", "id": -100}
            other_group = {"type": "supergroup", "id": -999}
            vcs_group = {"type": "supergroup", "id": -200}
            # The second group chat is open for /live on the same terms.
            self.assertTrue(_command_allowed(vcs_group, "333", "/live"))
            self.assertFalse(_command_allowed(vcs_group, "333", "/people"))
            self.assertFalse(_command_allowed(dm, "333", "/live"))
            self.assertTrue(_command_allowed(admin_dm, "111", "/live"))
            self.assertTrue(_command_allowed(admin_dm, "111", "/people"))
            self.assertTrue(_command_allowed(group, "333", "/live"))
            self.assertTrue(_command_allowed(group, "111", "/live"))
            self.assertFalse(_command_allowed(group, "333", "/people"))
            self.assertFalse(_command_allowed(group, "111", "/people"))
            self.assertFalse(_command_allowed(other_group, "333", "/live"))
            self.assertTrue(_callback_allowed(group, "333", "live:1:10"))
            self.assertFalse(_callback_allowed(group, "333", "who:1"))
            self.assertFalse(_callback_allowed(dm, "333", "live:1:10"))

    def test_live_card_skips_paused_and_projected_income(self):
        text = _live_card(
            "Gaurav",
            [
                _acct("Old", "Gaurav", 20, live=False, channel="kimi-old"),
                _acct("Gaurav1", "Gaurav", 3180.53, channel="kimi-k3-500k-proxy-20"),
                _acct("Gaurav2", "Gaurav", 500, channel="kimi-k3-500k-proxy-21"),
            ],
        )
        self.assertNotIn("Projected income", text)
        self.assertIn("<b>1.</b>", text)
        self.assertIn("<b>2.</b>", text)
        self.assertIn("kimi-k3-500k-proxy-20", text)
        self.assertIn("kimi-k3-500k-proxy-21", text)
        self.assertNotIn("kimi-old", text)
        self.assertNotIn("paused", text)
        groups = _live_groups(
            [
                _acct("Old", "Arif", 80, live=False),
                _acct("Live", "Gaurav", 50),
            ]
        )
        self.assertEqual([tag for tag, _ in groups], ["Gaurav"])
        self.assertIn(f"Disappears in {LIVE_MESSAGE_TTL_SECONDS}s", text)

    def test_live_buttons_use_stable_person_token(self):
        from app.services.telegram_bot import _group_by_token, _people_keyboard, _person_token

        groups = [("Gaurav", [_acct("G1", "Gaurav", 10)]), ("Arif", [_acct("A1", "Arif", 5)])]
        keyboard = _people_keyboard(groups, prefix="live", source_message_id=1090)
        first = keyboard["inline_keyboard"][0][0]["callback_data"]
        self.assertEqual(first, f"live:{_person_token('Gaurav')}:1090")
        self.assertEqual(_group_by_token(groups, _person_token("Arif"))[0], "Arif")
        self.assertIsNone(_group_by_token(groups, "missing"))

    def test_split_oversized_block(self):
        from app.services.telegram_bot import _split_telegram

        huge = "x" * 9000
        chunks = _split_telegram(huge, limit=4000)
        self.assertEqual(len(chunks), 3)
        self.assertTrue(all(len(chunk) <= 4000 for chunk in chunks))
        self.assertEqual("".join(chunks), huge)


class ProcessUpdate(unittest.IsolatedAsyncioTestCase):
    async def test_missing_note_helper_does_not_drop_the_command(self):
        from app.services.telegram_bot import _process_update

        settings = SimpleNamespace(
            telegram_admin_id_set={"111"},
            telegram_owner_id_set={"111"},
            telegram_chat_id="-100",
            telegram_vcs_chat_id="-200",
        )
        update = {
            "update_id": 1,
            "message": {
                "message_id": 9,
                "from": {"id": 111},
                "chat": {"id": 111, "type": "private"},
                "text": "/help",
            },
        }
        with patch("app.services.telegram_bot.get_settings", return_value=settings):
            with patch("app.services.telegram_bot.telegram_service.note_group_message_id", None):
                with patch(
                    "app.services.telegram_bot.telegram_service.send_message",
                    new_callable=AsyncMock,
                    return_value=20,
                ) as send:
                    await _process_update(update)
                    send.assert_awaited()

    async def test_result_edit_clears_keyboard(self):
        from app.services.telegram_bot import _replace_message

        with patch("app.services.telegram_bot.telegram_service.edit_message_text", new_callable=AsyncMock) as edit:
            await _replace_message(-100, 5, "done")
            edit.assert_awaited_once()
            self.assertEqual(edit.await_args.kwargs["reply_markup"], {"inline_keyboard": []})


class LiveEdit(unittest.IsolatedAsyncioTestCase):
    async def test_edit_treats_not_modified_as_success(self):
        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

            async def post(self, *_args, **_kwargs):
                return httpx.Response(
                    400,
                    json={
                        "ok": False,
                        "error_code": 400,
                        "description": "Bad Request: message is not modified: specified new message content and reply markup are exactly the same",
                    },
                )

        with patch("app.services.telegram_service.get_settings", return_value=SimpleNamespace(telegram_bot_token="t")):
            with patch("app.services.telegram_service.httpx.AsyncClient", return_value=FakeClient()):
                await telegram_service.edit_message_text(-100, 1, "same", reply_markup={"inline_keyboard": []})


class LiveSelfDestruct(unittest.IsolatedAsyncioTestCase):
    async def test_deletes_message_after_delay(self):
        with patch("app.services.telegram_bot.telegram_service.delete_message", new_callable=AsyncMock) as delete:
            task = schedule_self_destruct(-100, 42, delay=0.01)
            self.assertIsNotNone(task)
            await task
            delete.assert_awaited_once_with(-100, 42)

    async def test_reschedule_cancels_prior_timer(self):
        with patch("app.services.telegram_bot.telegram_service.delete_message", new_callable=AsyncMock) as delete:
            first = schedule_self_destruct(-100, 42, delay=5)
            second = schedule_self_destruct(-100, 42, delay=0.01)
            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            await second
            delete.assert_awaited_once_with(-100, 42)

    async def test_deletes_user_command_with_bot_reply(self):
        with patch("app.services.telegram_bot.telegram_service.delete_message", new_callable=AsyncMock) as delete:
            task = schedule_self_destruct(-100, 10, 42, delay=0.01)
            self.assertIsNotNone(task)
            await task
            self.assertEqual(delete.await_count, 2)
            delete.assert_any_await(-100, 10)
            delete.assert_any_await(-100, 42)

    async def test_cancel_all_self_destructs_drops_pending_timers(self):
        with patch("app.services.telegram_bot.telegram_service.delete_message", new_callable=AsyncMock) as delete:
            first = schedule_self_destruct(-100, 42, delay=5)
            second = schedule_self_destruct(-200, 7, delay=5)
            self.assertEqual(cancel_all_self_destructs(), 2)
            await asyncio.sleep(0)
            self.assertTrue(first.done())
            self.assertTrue(second.done())
            delete.assert_not_awaited()


class ClearMemberChats(unittest.IsolatedAsyncioTestCase):
    def _settings(self, **overrides):
        values = {
            "telegram_bot_token": "token",
            "telegram_chat_id": "-100",
            "telegram_vcs_chat_id": "-200",
            "telegram_admin_id_set": {"111", "222"},
            "telegram_owner_id_set": {"111"},
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def tearDown(self):
        telegram_service._clear_jobs.clear()
        telegram_service._clear_tasks.clear()
        telegram_service._group_high_water.clear()

    async def test_clear_chat_does_not_send_and_sweeps_every_id(self):
        with patch("app.services.telegram_service.send_message", new_callable=AsyncMock) as send:
            with patch("app.services.telegram_service.delete_messages", new_callable=AsyncMock, return_value=True) as delete:
                with patch("app.services.telegram_service._save_high_water", new_callable=AsyncMock):
                    result = await clear_chat(-100, kind="group", newest_id=250)
        send.assert_not_called()
        self.assertTrue(result["ok"])
        self.assertEqual(result["attempted"], 250)
        self.assertEqual(result["newest_id"], 250)
        batches = [call.args[1] for call in delete.await_args_list]
        self.assertEqual(batches[0], list(range(151, 251)))
        self.assertEqual(batches[1], list(range(51, 151)))
        self.assertEqual(batches[-1], list(range(1, 51)))
        self.assertEqual(sum(len(batch) for batch in batches), 250)

    async def test_failed_batch_falls_back_to_one_by_one(self):
        batch = httpx.Response(400, json={"ok": False, "description": "Bad Request: can't delete"})
        one = httpx.Response(200, json={"ok": True})

        class FakeClient:
            def __init__(self):
                self.calls = []

            async def post(self, url, json):
                self.calls.append((url, json))
                if url.endswith("deleteMessages"):
                    return batch
                return one

        fake = FakeClient()
        with patch("app.services.telegram_service.get_settings", return_value=self._settings()):
            ok = await telegram_service.delete_messages(-100, [1, 2, 3], client=fake)
        self.assertTrue(ok)
        self.assertTrue(any(url.endswith("deleteMessages") for url, _payload in fake.calls))
        self.assertEqual(sum(1 for url, _payload in fake.calls if url.endswith("deleteMessage")), 3)

    async def test_start_clears_only_the_linked_group(self):
        settings = self._settings()
        finished = {
            "chat_id": "-100",
            "kind": "group",
            "newest_id": 80,
            "attempted": 80,
            "failed_batches": 0,
            "ok": True,
        }
        with patch("app.services.telegram_service.get_settings", return_value=settings):
            with patch("app.services.telegram_service.send_message", new_callable=AsyncMock) as send:
                with patch("app.services.telegram_service.clear_chat", new_callable=AsyncMock, return_value=finished) as clear:
                    snap = await start_clear_group_chat()
                    self.assertTrue(snap["running"])
                    await telegram_service._clear_tasks["sb"]
        send.assert_not_called()
        clear.assert_awaited_once()
        self.assertEqual(clear.await_args.args[0], "-100")
        done = clear_group_snapshot()
        self.assertFalse(done["running"])
        self.assertTrue(done["ok"])
        self.assertEqual(done["attempted"], 80)

    async def test_send_still_posts_while_clear_runs(self):
        telegram_service._clear_jobs["sb"] = {"running": True}

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

            async def post(self, *_args, **_kwargs):
                return httpx.Response(200, json={"ok": True, "result": {"message_id": 9}})

        with patch("app.services.telegram_service.get_settings", return_value=self._settings()):
            with patch("app.services.telegram_service.httpx.AsyncClient", return_value=FakeClient()):
                sent = await telegram_service.send_message("/live still works", chat_id="-100")
        self.assertEqual(sent, 9)

    async def test_discover_tip_does_not_walk_every_id(self):
        async def probe(_session, _chat_id, message_id):
            return "deleted" if int(message_id) <= 80 else "missing"

        with patch("app.services.telegram_service._load_high_water", new_callable=AsyncMock, return_value=0):
            with patch("app.services.telegram_service._probe_one", new_callable=AsyncMock, side_effect=probe) as probed:
                telegram_service._group_high_water["sb"] = 0
                tip = await telegram_service._discover_tip(None, -100, "sb")
        self.assertGreaterEqual(tip, 80)
        self.assertLess(probed.await_count, 400)

    async def test_discover_tip_uses_high_water_without_full_search(self):
        async def probe(_session, _chat_id, message_id):
            return "deleted" if int(message_id) <= 80 else "missing"

        with patch("app.services.telegram_service._load_high_water", new_callable=AsyncMock, return_value=80):
            with patch("app.services.telegram_service._probe_one", new_callable=AsyncMock, side_effect=probe) as probed:
                telegram_service._group_high_water["sb"] = 80
                tip = await telegram_service._discover_tip(None, -100, "sb")
        self.assertEqual(tip, 80)
        self.assertLess(probed.await_count, 30)

    async def test_rejects_overlapping_clear(self):
        settings = self._settings()
        started = asyncio.Event()
        release = asyncio.Event()

        async def hold_clear(*_args, **_kwargs):
            started.set()
            await release.wait()
            return {"ok": True, "newest_id": 5, "attempted": 5, "failed_batches": 0, "kind": "group", "chat_id": "-100"}

        with patch("app.services.telegram_service.get_settings", return_value=settings):
            with patch("app.services.telegram_service.clear_chat", new_callable=AsyncMock, side_effect=hold_clear):
                first = await start_clear_group_chat()
                self.assertTrue(first["running"])
                await started.wait()
                with self.assertRaises(TelegramError):
                    await start_clear_group_chat()
                release.set()
                await telegram_service._clear_tasks["sb"]
        self.assertFalse(clear_group_snapshot()["running"])


class WebhookUrl(unittest.TestCase):
    def test_appends_path_and_accepts_full_url(self):
        from app.services.telegram_bot import webhook_public_url, webhook_secret_ok

        with patch(
            "app.services.telegram_bot.get_settings",
            return_value=SimpleNamespace(telegram_webhook_url="https://portal.example"),
        ):
            self.assertEqual(webhook_public_url(), "https://portal.example/api/telegram/webhook")
        with patch(
            "app.services.telegram_bot.get_settings",
            return_value=SimpleNamespace(telegram_webhook_url="https://portal.example/api/telegram/webhook"),
        ):
            self.assertEqual(webhook_public_url(), "https://portal.example/api/telegram/webhook")
        with patch(
            "app.services.telegram_bot.get_settings",
            return_value=SimpleNamespace(telegram_webhook_secret="abc", jwt_secret="x"),
        ):
            self.assertTrue(webhook_secret_ok("abc"))
            self.assertFalse(webhook_secret_ok("nope"))
            self.assertFalse(webhook_secret_ok(""))
            self.assertFalse(webhook_secret_ok(None))

    def test_certificate_upload_uses_pem_filename(self):
        from app.services.telegram_bot import _WEBHOOK_CERT_UPLOAD_NAME, webhook_certificate_file

        self.assertEqual(_WEBHOOK_CERT_UPLOAD_NAME, "PUBLIC.pem")
        with patch("app.services.telegram_bot._WEBHOOK_CERT") as cert_path:
            cert_path.is_file.return_value = True
            cert_path.read_bytes.return_value = b"-----BEGIN CERTIFICATE-----\n"
            name, data, content_type = webhook_certificate_file()
        self.assertEqual(name, "PUBLIC.pem")
        self.assertTrue(data.startswith(b"-----BEGIN CERTIFICATE-----"))
        self.assertEqual(content_type, "application/x-pem-file")
