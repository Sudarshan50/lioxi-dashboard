import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services import telegram_bot as bot
from app.services.telegram_bot import (
    ACCOUNT_PAGE_SIZE,
    _account_keyboard,
    _account_page,
    _account_page_count,
    _people_keyboard,
    _picker_caption,
    _toggle_candidates,
)


def _acct(account_id: int, name: str, *, status: int = 2, gateway: str = "O1", spend: float = 0):
    return SimpleNamespace(
        id=account_id,
        name=name,
        new_api_status=status,
        new_api_gateway=gateway,
        new_api_name=name,
        new_api_cost_usd=spend,
        credits_limit=10000,
        credits_currency="USD",
        credits_available=True,
    )


class AccountPickerPages(unittest.TestCase):
    def test_enable_candidates_need_a_disabled_gateway(self):
        rows = [
            _acct(1, "On", status=1),
            _acct(2, "Off"),
            _acct(3, "NoGateway", gateway=""),
            _acct(4, "Never", status=None),
        ]
        self.assertEqual([a.name for a in _toggle_candidates(rows, True)], ["Off"])
        self.assertEqual([a.name for a in _toggle_candidates(rows, False)], ["On"])

    def test_keyboard_pages_when_over_telegram_button_limit(self):
        accounts = [_acct(i, f"Acc{i}") for i in range(1, 160)]
        self.assertGreater(_account_page_count(len(accounts)) * ACCOUNT_PAGE_SIZE, 100)
        first = _account_keyboard(accounts, prefix="en", page=0)
        last_page = _account_page_count(len(accounts)) - 1
        last = _account_keyboard(accounts, prefix="en", page=last_page)
        first_rows = first["inline_keyboard"]
        self.assertEqual(first_rows[-1], [{"text": "✖️ Cancel", "callback_data": "cancel"}])
        self.assertEqual(first_rows[-2][0]["text"], "1/" + str(last_page + 1))
        self.assertEqual(first_rows[-2][-1]["callback_data"], "enp:1")
        self.assertTrue(any(btn.get("text") == "Next ›" for btn in first_rows[-2]))
        self.assertFalse(any(btn.get("text") == "‹ Prev" for btn in first_rows[-2]))
        self.assertTrue(any(btn.get("text") == "‹ Prev" for btn in last["inline_keyboard"][-2]))
        picks = [btn["callback_data"] for row in first_rows[:-2] for btn in row]
        self.assertEqual(len(picks), ACCOUNT_PAGE_SIZE)
        self.assertTrue(all(item.startswith("en:") for item in picks))
        self.assertLessEqual(sum(len(row) for row in first_rows), 100)

    def test_page_clamps_and_caption(self):
        accounts = [_acct(i, f"Acc{i}") for i in range(20)]
        slice_rows, page, pages = _account_page(accounts, 99)
        self.assertEqual(page, pages - 1)
        self.assertEqual(len(slice_rows), 20 - ACCOUNT_PAGE_SIZE)
        self.assertIn("1/2", _picker_caption("en", 20, 0))
        self.assertIn("159", _picker_caption("en", 159, 0))

    def test_enable_lists_lowest_usage_first(self):
        accounts = [
            _acct(1, "Hot", spend=9000),
            _acct(2, "Cold", spend=100),
            _acct(3, "Mid", spend=4000),
        ]
        names = [btn["text"] for row in _account_keyboard(accounts, prefix="en")["inline_keyboard"][:-1] for btn in row]
        self.assertEqual([name.split(" · ")[0].replace("⛔ ", "") for name in names], ["Cold", "Mid", "Hot"])

    def test_people_and_live_pickers_page(self):
        groups = [(f"Person{i}", [_acct(i, f"Person{i}", status=1)]) for i in range(40)]
        first = _people_keyboard(groups, prefix="who", page=0)
        live = _people_keyboard(groups, prefix="live", source_message_id=9, page=1)
        self.assertEqual(first["inline_keyboard"][-2][-1]["callback_data"], "whop:1")
        self.assertTrue(any(btn.get("text") == "‹ Prev" for btn in live["inline_keyboard"][-2]))
        self.assertTrue(any(btn["callback_data"].startswith("livep:0:9") for btn in live["inline_keyboard"][-2]))
        names = [btn["text"] for row in first["inline_keyboard"][:-2] for btn in row]
        self.assertEqual(len(names), ACCOUNT_PAGE_SIZE)
        self.assertNotIn("Person20", names)

    def test_group_chat_can_page_live(self):
        chat = {"type": "supergroup", "id": "-100"}
        with patch.object(bot, "_group_open", return_value=True), patch.object(
            bot, "_chat_allowed", return_value=False
        ):
            self.assertTrue(bot._callback_allowed(chat, "not-admin", "livep:1"))
            self.assertFalse(bot._callback_allowed(chat, "not-admin", "whop:1"))
            self.assertFalse(bot._callback_allowed(chat, "not-admin", "enp:1"))


if __name__ == "__main__":
    unittest.main()
