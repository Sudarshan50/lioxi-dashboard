import unittest

from app.services.kimi_deploy_service import _copy_deploy_outputs
from app.services.kimi_newapi import (
    KIMI_CHANNEL_GROUP,
    KIMI_CHANNEL_MODELS,
    KIMI_PARAM_OVERRIDE,
    KIMI_POOL_TAG,
    _channel_create_body,
    _channel_update_body,
    foundry_key_from_account,
    pool_tag,
    pool_tag_variants,
)


def _row(name: str, tag: str) -> dict:
    return {"name": name, "tag": tag}


class PoolTagExact(unittest.TestCase):
    def test_canonical_has_leading_space_only(self):
        self.assertEqual(KIMI_POOL_TAG, " kimi-k3-pool")
        self.assertNotEqual(KIMI_POOL_TAG, " kimi-k3-pool ")

    def test_majority_exact_string_wins(self):
        channels = [_row(f"kimi-k3-500k-proxy-{i}", " kimi-k3-pool") for i in range(5)]
        channels += [_row(f"kimi-k3-500k-proxy-{i + 10}", " kimi-k3-pool ") for i in range(2)]
        self.assertEqual(pool_tag(channels), " kimi-k3-pool")
        self.assertEqual(pool_tag_variants(channels), [" kimi-k3-pool "])

    def test_trailing_space_variant_is_a_second_tag(self):
        channels = [
            _row("kimi-k3-500k-proxy-1", " kimi-k3-pool"),
            _row("kimi-k3-500k-proxy-2", " kimi-k3-pool "),
        ]
        self.assertEqual(pool_tag(channels), " kimi-k3-pool")
        self.assertEqual(pool_tag_variants(channels, " kimi-k3-pool"), [" kimi-k3-pool "])

    def test_create_and_update_write_the_live_tag(self):
        body = _channel_create_body(
            name="kimi-k3-500k-proxy-1",
            api_key="k",
            base_url="https://x.openai.azure.com",
            priority=10,
            weight=1,
            tag=" kimi-k3-pool",
        )
        self.assertEqual(body["channel"]["tag"], " kimi-k3-pool")
        self.assertEqual(body["mode"], "single")
        updated = _channel_update_body(
            {"id": 1, "tag": " kimi-k3-pool "},
            "kimi-k3-500k-proxy-1",
            tag=" kimi-k3-pool",
        )
        self.assertEqual(updated["tag"], " kimi-k3-pool")

    def test_update_keeps_existing_tag_when_omitted(self):
        updated = _channel_update_body({"id": 1, "tag": " kimi-k3-pool"}, "n")
        self.assertEqual(updated["tag"], " kimi-k3-pool")

    def test_create_body_sets_model_group_and_content_filter(self):
        body = _channel_create_body(
            name="kimi-k3-500k-proxy-9",
            api_key="k",
            base_url="https://x.openai.azure.com",
            priority=10,
            weight=1,
        )["channel"]
        self.assertEqual(body["models"], KIMI_CHANNEL_MODELS)
        self.assertEqual(body["models"], "FW-Kimi-K3")
        self.assertEqual(body["group"], KIMI_CHANNEL_GROUP)
        self.assertEqual(body["type"], 3)
        self.assertIn("video", KIMI_PARAM_OVERRIDE)
        self.assertEqual(body["param_override"], KIMI_PARAM_OVERRIDE)
        self.assertEqual(body["tag"], KIMI_POOL_TAG)


class FoundryKeyFallback(unittest.TestCase):
    def test_reads_in_memory_deploy_key(self):
        self.assertEqual(foundry_key_from_account({"api_key": " sk-live "}), "sk-live")
        self.assertEqual(foundry_key_from_account({"Key1": "from-azure"}), "from-azure")
        self.assertEqual(foundry_key_from_account({}), "")

    def test_copy_deploy_outputs_keeps_key_for_newapi(self):
        accounts = [{"AZURE_SUBSCRIPTION_ID": "sub"}]
        _copy_deploy_outputs(
            accounts,
            [
                {
                    "account_name": "example-resource",
                    "azure_openai_endpoint": "https://example-resource.openai.azure.com",
                    "resource_group": "rg",
                    "api_key": "foundry-key",
                    "subscription_id": "sub",
                }
            ],
        )
        self.assertEqual(accounts[0]["account_name"], "example-resource")
        self.assertEqual(accounts[0]["api_key"], "foundry-key")
        self.assertEqual(accounts[0]["azure_openai_endpoint"], "https://example-resource.openai.azure.com")
