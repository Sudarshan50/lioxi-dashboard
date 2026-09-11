import unittest

from app.services.google_sheet_inventory import parse_compact
from app.services.kimi_capacity import live_newapi_hosts, rpm_from_tpm, sum_live_capacity


class LiveHosts(unittest.TestCase):
    def test_only_enabled_unique_hosts(self):
        channels = [
            {"status": 1, "name": "kimi-k3-500k-proxy-1", "tag": " kimi-k3-pool", "base_url": "https://one.openai.azure.com"},
            {"status": 2, "name": "kimi-k3-500k-proxy-2", "tag": " kimi-k3-pool", "base_url": "https://two.openai.azure.com"},
            {"status": 3, "name": "kimi-k3-500k-proxy-3", "tag": " kimi-k3-pool", "base_url": "https://four.openai.azure.com"},
            {"status": 1, "name": "kimi-k3-500k-proxy-1", "tag": " kimi-k3-pool", "base_url": "https://one.cognitiveservices.azure.com"},
            {"status": 1, "name": "other-proxy-9", "tag": "other", "base_url": "https://five.openai.azure.com"},
            {"status": 1, "name": "kimi-k3-500k-proxy-4", "tag": " kimi-k3-pool", "base_url": "https://three.openai.azure.com"},
        ]
        self.assertEqual(live_newapi_hosts(channels), ["one", "three"])

    def test_skips_missing_host(self):
        self.assertEqual(live_newapi_hosts([{"status": 1, "name": "kimi-k3-500k-proxy-1", "base_url": ""}]), [])


class CapacitySum(unittest.TestCase):
    def test_parse_compact(self):
        self.assertEqual(parse_compact("500k"), 500_000)
        self.assertEqual(parse_compact("1.2M"), 1_200_000)
        self.assertEqual(parse_compact("12,000"), 12_000)
        self.assertIsNone(parse_compact(""))

    def test_rpm_from_tpm(self):
        self.assertEqual(rpm_from_tpm(500_000), 500)
        self.assertEqual(rpm_from_tpm(50), 50)

    def test_sums_only_live_hosts(self):
        totals = sum_live_capacity(
            ["one", "two"],
            {"one": (50_000, 50), "two": (100_000, 100), "disabled": (500_000, 500)},
        )
        self.assertEqual(totals, {"tpm": 150_000, "rpm": 150, "accounts": 2})

    def test_skips_live_host_without_quota(self):
        totals = sum_live_capacity(["one", "two"], {"one": (50_000, 50)})
        self.assertEqual(totals, {"tpm": 50_000, "rpm": 50, "accounts": 1})


if __name__ == "__main__":
    unittest.main()
