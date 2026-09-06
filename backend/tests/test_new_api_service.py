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
)


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
