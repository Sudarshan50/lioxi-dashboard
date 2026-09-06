import unittest

from app.schemas.kimi_deploy import KimiDeployResult
from app.services.google_sheet_inventory import format_compact
from app.services.kimi_deploy_service import (
    attach_quota_fields,
    fireworks_limit_from_usages,
    normalize_quota_tier_name,
    quota_tier_from_arm,
    quota_tier_number,
)


class AzureQuotaTier(unittest.TestCase):
    def test_normalizes_azure_names(self):
        self.assertEqual(normalize_quota_tier_name("Tier 1"), "Tier 1")
        self.assertEqual(normalize_quota_tier_name("Tier-1"), "Tier 1")
        self.assertEqual(normalize_quota_tier_name("tier_2"), "Tier 2")
        self.assertEqual(normalize_quota_tier_name("Free-Tier"), "Free Tier")
        self.assertEqual(normalize_quota_tier_name("Free Tier"), "Free Tier")
        self.assertIsNone(normalize_quota_tier_name(""))
        self.assertIsNone(normalize_quota_tier_name(None))

    def test_sort_order(self):
        self.assertEqual(quota_tier_number("Free Tier"), 0)
        self.assertEqual(quota_tier_number("Tier 1"), 1)
        self.assertEqual(quota_tier_number("Tier 6"), 6)

    def test_parses_arm_payload(self):
        current, nxt, available = quota_tier_from_arm(
            {
                "properties": {
                    "currentTierName": "Free-Tier",
                    "tierUpgradeEligibilityInfo": {
                        "nextTierName": "Tier-1",
                        "upgradeAvailabilityStatus": "Available",
                    },
                }
            }
        )
        self.assertEqual(current, "Free Tier")
        self.assertEqual(nxt, "Tier 1")
        self.assertTrue(available)

    def test_parses_list_payload(self):
        current, nxt, available = quota_tier_from_arm(
            {"value": [{"properties": {"currentTierName": "Tier 2"}}]}
        )
        self.assertEqual(current, "Tier 2")
        self.assertIsNone(nxt)
        self.assertFalse(available)


class FireworksQuota(unittest.TestCase):
    def test_reads_limit(self):
        body = {
            "value": [
                {"name": {"value": "OpenAI.Standard.gpt-4"}, "limit": 10},
                {"name": {"value": "AIServices.DataZoneStandard.Fireworks"}, "limit": 500},
            ]
        }
        self.assertEqual(fireworks_limit_from_usages(body), 500)

    def test_missing(self):
        self.assertIsNone(fireworks_limit_from_usages({"value": []}))
        self.assertIsNone(fireworks_limit_from_usages(None))


class QuotaUpgrade(unittest.TestCase):
    def test_flags_when_quota_grew(self):
        result = KimiDeployResult(ok=True, capacity=50, tpm=50_000, rpm=50, account_tier="Tier 1")
        attach_quota_fields(result, 500)
        self.assertEqual(result.tpm_available, 500_000)
        self.assertEqual(result.rpm_available, 500)
        self.assertTrue(result.tpm_upgrade_available)
        self.assertEqual(result.account_tier, "Tier 1")

    def test_no_flag_when_already_at_max(self):
        result = KimiDeployResult(ok=True, capacity=500, tpm=500_000, rpm=500)
        attach_quota_fields(result, 500)
        self.assertFalse(result.tpm_upgrade_available)

    def test_subtracts_other_deployments(self):
        result = KimiDeployResult(ok=True, capacity=50, tpm=50_000, rpm=50)
        attach_quota_fields(result, 500, used_by_others=400)
        self.assertEqual(result.tpm_available, 100_000)
        self.assertTrue(result.tpm_upgrade_available)

    def test_sheet_tpm_follows_upgraded_value(self):
        self.assertEqual(format_compact(50_000), "50k")
        self.assertEqual(format_compact(500_000), "500k")


if __name__ == "__main__":
    unittest.main()
