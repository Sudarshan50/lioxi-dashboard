import unittest
from types import SimpleNamespace

from app.services.new_api_service import (
    _account_key,
    _apply_fetched_portals,
    _host_key,
    _merge_channel_page,
    _portal_quota,
    _portal_status,
    _unique_channels,
    channels_matching_account,
)
from app.services.channel_cards import cards_for_channels


class ChannelsMatchingAccount(unittest.TestCase):
    def test_matches_new_host_after_redeploy(self):
        account = SimpleNamespace(
            resource_name="lioxishaurya8-kimi-w556kl",
            endpoint="https://lioxishaurya8-kimi-w556kl.openai.azure.com/",
            new_api_channel_id=223,
            new_api_name="kimi-k3-500k-proxy-186",
        )
        channels = [
            {
                "id": 223,
                "name": "kimi-k3-500k-proxy-186",
                "base_url": "https://lioxishaurya8-kimi-w556kl.openai.azure.com",
                "status": 2,
            }
        ]
        self.assertEqual(channels_matching_account(account, channels)[0]["id"], 223)

    def test_falls_back_to_channel_id_when_host_still_old(self):
        account = SimpleNamespace(
            resource_name="lioxishaurya8-kimi-w556kl",
            endpoint="https://lioxishaurya8-kimi-w556kl.openai.azure.com/",
            new_api_channel_id=223,
            new_api_name="kimi-k3-500k-proxy-186",
        )
        channels = [
            {
                "id": 223,
                "name": "kimi-k3-500k-proxy-186",
                "base_url": "https://lioxishaurya8-kimi-t6vw3v.openai.azure.com",
                "status": 2,
            }
        ]
        self.assertEqual(channels_matching_account(account, channels)[0]["id"], 223)


class UniqueChannels(unittest.TestCase):
    def test_drops_duplicate_ids_and_keeps_first(self):
        channels = [
            {"id": 2103, "used_quota": 3716409712, "status": 2, "name": "kimi-k3-500k-proxy-10"},
            {"id": 2103, "used_quota": 3716409712, "status": 2, "name": "kimi-k3-500k-proxy-10"},
            {"id": 9, "used_quota": 50, "status": 1, "name": "other"},
        ]
        unique = _unique_channels(channels)
        self.assertEqual([channel["id"] for channel in unique], [2103, 9])

    def test_portal_quota_counts_each_id_once(self):
        channels = [
            {"id": 2103, "used_quota": 100},
            {"id": 2103, "used_quota": 100},
            {"id": 9, "used_quota": 50},
        ]
        self.assertEqual(_portal_quota(channels), 150)

    def test_portal_status_ignores_duplicate_rows(self):
        channels = [
            {"id": 1, "status": 2},
            {"id": 1, "status": 2},
        ]
        self.assertEqual(_portal_status(_unique_channels(channels)), 2)


class KeepLastKnownPortal(unittest.TestCase):
    def _account(self, **overrides):
        base = dict(
            new_api_gateway="O1+O2",
            new_api_cost_o1_usd=2500.0,
            new_api_cost_o2_usd=7500.0,
            new_api_status_o1=2,
            new_api_status_o2=2,
            new_api_status=2,
            new_api_used_quota=None,
        )
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_missing_o2_this_fetch_keeps_stored_o2(self):
        account = self._account()
        _apply_fetched_portals(
            account,
            {"O1", "O2"},
            {"O1": [{"id": 1, "used_quota": 1_250_000_000, "status": 2}]},
        )
        self.assertEqual(account.new_api_gateway, "O1+O2")
        self.assertEqual(account.new_api_cost_o2_usd, 7500.0)
        self.assertGreater(account.new_api_cost_usd, 9000)

    def test_seen_o2_updates_spend(self):
        account = self._account()
        _apply_fetched_portals(
            account,
            {"O1", "O2"},
            {
                "O1": [{"id": 1, "used_quota": 1_250_000_000, "status": 2}],
                "O2": [{"id": 2, "used_quota": 3_750_000_000, "status": 2}],
            },
        )
        self.assertEqual(account.new_api_gateway, "O1+O2")
        self.assertAlmostEqual(account.new_api_cost_o2_usd, 7500.0)
        self.assertAlmostEqual(account.new_api_cost_usd, 10000.0)

    def test_o1_outage_keeps_stored_o1(self):
        account = self._account()
        _apply_fetched_portals(
            account,
            {"O2"},
            {"O2": [{"id": 2, "used_quota": 3_750_000_000, "status": 2}]},
        )
        self.assertEqual(account.new_api_gateway, "O1+O2")
        self.assertEqual(account.new_api_cost_o1_usd, 2500.0)
        self.assertAlmostEqual(account.new_api_cost_o2_usd, 7500.0)

    def test_first_o2_match_adds_portal(self):
        account = self._account(new_api_gateway="O1", new_api_cost_o2_usd=None, new_api_status_o2=None)
        _apply_fetched_portals(
            account,
            {"O1", "O2"},
            {
                "O1": [{"id": 1, "used_quota": 1_250_000_000, "status": 2}],
                "O2": [{"id": 2, "used_quota": 3_750_000_000, "status": 2}],
            },
        )
        self.assertEqual(account.new_api_gateway, "O1+O2")
        self.assertAlmostEqual(account.new_api_cost_o2_usd, 7500.0)
        self.assertAlmostEqual(account.new_api_cost_usd, 10000.0)


class HostAndPages(unittest.TestCase):
    def test_host_key_joins_openai_and_cognitiveservices(self):
        self.assertEqual(_host_key("https://example-resource.openai.azure.com"), "example-resource")
        self.assertEqual(
            _host_key("https://example-resource.cognitiveservices.azure.com/"),
            "example-resource",
        )
        account = SimpleNamespace(resource_name="example-resource", endpoint="https://other.openai.azure.com")
        self.assertEqual(_account_key(account), "example-resource")

    def test_merge_page_skips_duplicate_ids(self):
        by_id: dict[int, dict] = {}
        self.assertEqual(_merge_channel_page(by_id, [{"id": 2101, "used_quota": 1}, {"id": 2101, "used_quota": 9}]), 1)
        self.assertEqual(_merge_channel_page(by_id, [{"id": 2101, "used_quota": 9}]), 0)
        self.assertEqual(_merge_channel_page(by_id, [{"id": 16, "used_quota": 2}]), 1)
        self.assertEqual(len(by_id), 2)


class ChannelCards(unittest.TestCase):
    def test_same_host_keeps_separate_status_and_spend(self):
        channels = [
            {
                "id": 10,
                "name": "kimi-k3-proxy",
                "tag": " kimi-k3-pool",
                "base_url": "https://res-a.openai.azure.com",
                "status": 1,
                "used_quota": 2_500_000_000,
            },
            {
                "id": 11,
                "name": "gpt-astra-proxy2",
                "tag": "gpt-astra",
                "base_url": "https://res-a.openai.azure.com/",
                "status": 2,
                "used_quota": 8_500_000,
            },
        ]
        cards = cards_for_channels(channels, "O1", 500_000)["res-a"]
        self.assertEqual([card["name"] for card in cards], ["kimi-k3-proxy", "gpt-astra-proxy2"])
        self.assertEqual(cards[0]["role"], "primary")
        self.assertEqual(cards[0]["status"], 1)
        self.assertAlmostEqual(cards[0]["spend_usd"], 5000)
        self.assertEqual(cards[1]["role"], "gpt")
        self.assertEqual(cards[1]["status"], 2)
        self.assertAlmostEqual(cards[1]["spend_usd"], 17)
        self.assertAlmostEqual(cards[0]["spend_usd"] + cards[1]["spend_usd"], 5017)
