import unittest
from types import SimpleNamespace

from app.services.gpt_catalog import (
    GPT_CHANNEL_GROUP,
    GPT_GROUP_PATCHES,
    GPT_MODEL_NAMES,
    GPT_PARAM_OVERRIDE,
    GPT_PRICE_PATCHES,
    index_existing_deployments,
    merge_model_list,
    merge_price_maps,
    next_gpt_channel_name,
    plan_model,
    resolve_requested_models,
    select_catalog_model,
)
from app.runtime import AZURE_SYNC_CONCURRENCY
from app.services.gpt_deploy_service import (
    _unique_ids,
    bulk_workers,
    desired_channel_status,
    eligibility_error,
    gpt_channels_by_host,
    gpt_deployed_hosts,
    match_gpt_channel,
)
from app.services.new_api_service import _portal_quota, channels_matching_account


def _model(name="gpt-5.5", version="2026-04-24", default=True):
    return {
        "format": "OpenAI",
        "name": name,
        "version": version,
        "isDefaultVersion": default,
        "skus": [
            {
                "name": "GlobalStandard",
                "usageName": f"OpenAI.GlobalStandard.{name}",
                "capacity": {"maximum": 1_000_000},
                "rateLimits": [
                    {"key": "request", "renewalPeriod": 60, "count": 1},
                    {"key": "token", "renewalPeriod": 60, "count": 1000},
                ],
            },
            {
                "name": "DataZoneStandard",
                "usageName": f"OpenAI.DataZoneStandard.{name}",
                "capacity": {"maximum": 1_000_000},
                "rateLimits": [
                    {"key": "request", "renewalPeriod": 60, "count": 1},
                    {"key": "token", "renewalPeriod": 60, "count": 1000},
                ],
            },
        ],
    }


def _usages(name="gpt-5.5", global_limit=1000, global_used=0, zone_limit=333, zone_used=0):
    return [
        {"name": {"value": f"OpenAI.GlobalStandard.{name}"}, "limit": global_limit, "currentValue": global_used},
        {"name": {"value": f"OpenAI.DataZoneStandard.{name}"}, "limit": zone_limit, "currentValue": zone_used},
    ]


class GptCatalogTests(unittest.TestCase):
    def test_stack_is_the_full_model_list(self):
        self.assertEqual(resolve_requested_models(True, []), list(GPT_MODEL_NAMES))

    def test_unknown_model_is_rejected(self):
        with self.assertRaises(ValueError):
            resolve_requested_models(False, ["gpt-4o"])

    def test_picks_global_standard_for_max_tpm(self):
        plan = plan_model(_model(), _usages())
        self.assertTrue(plan["available"])
        self.assertEqual(plan["sku"], "GlobalStandard")
        self.assertEqual(plan["capacity"], 1000)
        self.assertEqual(plan["tpm"], 1_000_000)
        self.assertEqual(plan["rpm"], 1000)

    def test_existing_deployment_capacity_is_not_double_counted(self):
        plan = plan_model(
            _model(),
            _usages(global_used=400),
            {"sku": "GlobalStandard", "capacity": 400},
        )
        self.assertEqual(plan["capacity"], 1000)
        self.assertEqual(plan["tpm"], 1_000_000)

    def test_other_usage_reduces_capacity(self):
        plan = plan_model(_model(), _usages(global_used=400))
        self.assertEqual(plan["capacity"], 600)
        self.assertEqual(plan["tpm"], 600_000)
        self.assertEqual(plan["rpm"], 600)

    def test_falls_back_to_datazone_when_global_is_full(self):
        plan = plan_model(_model(), _usages(global_limit=1000, global_used=1000, zone_limit=333, zone_used=0))
        self.assertEqual(plan["sku"], "DataZoneStandard")
        self.assertEqual(plan["capacity"], 333)
        self.assertEqual(plan["tpm"], 333_000)

    def test_missing_catalog_model(self):
        plan = plan_model(None, [])
        self.assertFalse(plan["available"])
        self.assertIn("catalog", plan["reason"])

    def test_selects_default_version(self):
        items = [
            {"model": _model(version="2026-01-01", default=False)},
            {"model": _model(version="2026-04-24", default=True)},
        ]
        chosen = select_catalog_model(items, "gpt-5.5")
        self.assertEqual(chosen["version"], "2026-04-24")

    def test_channel_name_and_model_merge(self):
        channels = [{"name": "gpt-astra-proxy0"}, {"name": "gpt-astra-proxy1"}, {"name": "kimi-k3-500k-proxy-3"}]
        self.assertEqual(next_gpt_channel_name(channels), "gpt-astra-proxy2")
        self.assertEqual(merge_model_list("gpt-6-astra", ["gpt-5.5", "gpt-6-astra"]), "gpt-6-astra,gpt-5.5")

    def test_indexes_only_gpt_deployments(self):
        rows = index_existing_deployments(
            [
                {"name": "FW-Kimi-K3", "sku": {"name": "DataZoneStandard", "capacity": 500}, "properties": {"model": {"name": "FW-Kimi-K3"}}},
                {"name": "gpt-5.5", "sku": {"name": "GlobalStandard", "capacity": 1000}, "properties": {"model": {"name": "gpt-5.5", "version": "2026-04-24"}, "provisioningState": "Succeeded"}},
            ]
        )
        self.assertEqual(set(rows), {"gpt-5.5"})
        self.assertEqual(rows["gpt-5.5"]["capacity"], 1000)


class GptNewApiFixTests(unittest.TestCase):
    def test_sol_price_is_added_without_replacing_astra(self):
        current = {"gpt-6-astra": 0.6, "gpt-6-sol": 9}
        merged = merge_price_maps(current, GPT_PRICE_PATCHES["ModelRatio"])
        self.assertEqual(merged["gpt-6-astra"], 0.6)
        self.assertEqual(merged["gpt-6-sol"], 9)
        fresh = merge_price_maps({}, GPT_PRICE_PATCHES["ModelRatio"])
        self.assertEqual(fresh["gpt-6-sol"], 1)
        self.assertIn("gpt-6-sol", GPT_PRICE_PATCHES["billing_setting.billing_expr"])

    def test_group_is_gpt_stack_and_existing_groups_stay(self):
        self.assertEqual(GPT_CHANNEL_GROUP, "gpt-stack")
        merged = merge_price_maps({"gpt-6": 1.12, "default": 1}, GPT_GROUP_PATCHES["GroupRatio"])
        self.assertEqual(merged["gpt-6"], 1.12)
        self.assertEqual(merged["gpt-stack"], 1)

    def test_token_override_moves_max_tokens(self):
        self.assertIn("max_completion_tokens", GPT_PARAM_OVERRIDE)
        self.assertIn('"from":"max_tokens"', GPT_PARAM_OVERRIDE)


class GptBulkTests(unittest.TestCase):
    def test_ids_are_deduped_in_order(self):
        self.assertEqual(_unique_ids([9, 9, 4, 9, 1]), [9, 4, 1])
        self.assertEqual(_unique_ids([]), [])

    def test_batch_size_follows_the_azure_cap(self):
        self.assertEqual(bulk_workers(1), 1)
        self.assertEqual(bulk_workers(3), 3)
        self.assertEqual(bulk_workers(100), AZURE_SYNC_CONCURRENCY)


class GptAccountGateTests(unittest.TestCase):
    def test_only_10k_accounts(self):
        ten = SimpleNamespace(name="Lioxi-Yuvraj12", credits_limit=10000, credits_currency="USD", blocked=False)
        one = SimpleNamespace(name="small", credits_limit=1000, credits_currency="USD", blocked=False)
        self.assertIsNone(eligibility_error(ten))
        self.assertIn("10k", eligibility_error(one) or "")

    def test_blocked_account_is_rejected(self):
        account = SimpleNamespace(name="Lioxi-Yuvraj12", credits_limit=10000, credits_currency="USD", blocked=True)
        self.assertIn("blocked", eligibility_error(account) or "")

    def test_stopped_account_creates_a_disabled_channel(self):
        live = SimpleNamespace(blocked=False, new_api_status=1)
        stopped = SimpleNamespace(blocked=False, new_api_status=2)
        self.assertEqual(desired_channel_status(live, False), 1)
        self.assertEqual(desired_channel_status(live, True), 2)
        self.assertEqual(desired_channel_status(stopped, False), 2)

    def test_matches_gpt_channel_on_the_same_host(self):
        channels = [
            {"name": "kimi-k3-500k-proxy-298", "base_url": "https://res.openai.azure.com", "tag": " kimi-k3-pool"},
            {"name": "gpt-astra-proxy2", "base_url": "https://res.openai.azure.com", "tag": "gpt-astra"},
        ]
        match = match_gpt_channel(channels, "res")
        self.assertEqual(match["name"], "gpt-astra-proxy2")
        self.assertIsNone(match_gpt_channel(channels, "other"))

    def test_deployed_hosts_come_from_gpt_channels(self):
        channels = [
            {"name": "kimi-k3-500k-proxy-1", "base_url": "https://res-a.openai.azure.com", "tag": " kimi-k3-pool"},
            {"name": "gpt-astra-proxy2", "base_url": "https://res-a.openai.azure.com", "tag": "gpt-astra"},
            {"name": "cs-proxy-4", "base_url": "https://res-b.openai.azure.com", "tag": ""},
        ]
        self.assertEqual(gpt_deployed_hosts(channels), {"res-a"})
        self.assertEqual(gpt_channels_by_host(channels), {"res-a": "gpt-astra-proxy2"})


class CombinedSpendTests(unittest.TestCase):
    def test_gpt_channel_spend_is_added_under_the_same_account(self):
        account = SimpleNamespace(
            resource_name="lioxiyuvraj12-kimi-etzna4",
            endpoint="https://lioxiyuvraj12-kimi-etzna4.openai.azure.com",
            new_api_channel_id=10,
            new_api_name="kimi-k3-500k-proxy-298",
        )
        channels = [
            {"id": 10, "name": "kimi-k3-500k-proxy-298", "base_url": "https://lioxiyuvraj12-kimi-etzna4.openai.azure.com", "used_quota": 500_000},
            {"id": 20, "name": "gpt-astra-proxy2", "tag": "gpt-astra", "base_url": "https://lioxiyuvraj12-kimi-etzna4.openai.azure.com", "used_quota": 125_000},
        ]
        matched = channels_matching_account(account, channels)
        self.assertEqual(len(matched), 2)
        self.assertEqual(_portal_quota(matched), 625_000)


if __name__ == "__main__":
    unittest.main()
