import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.schemas.kimi_deploy import KimiDeployResult
from app.services.quota_autoscale import (
    AutoscaleCandidate,
    TWO_MILLION_TPM,
    UPGRADED_NEWAPI_WEIGHT,
    _apply_auto_quota,
    _detail,
    _raise_newapi_weights,
    _should_scale,
    is_kimi_k3_account,
    is_live_newapi_enabled,
    is_newapi_enabled,
    needs_upgraded_weight,
    reached_2m_tpm,
    select_autoscale_targets,
)


def _portal(**overrides):
    row = SimpleNamespace(
        resource_group="rg-alex-kimi",
        resource_name="alex-kimi-n123",
        new_api_name="kimi-k3-500k-proxy-12",
        new_api_gateway="O1",
        new_api_status=1,
        new_api_status_o1=1,
        new_api_weight=1,
        new_api_channel_id=None,
        subscription_id="sub-1",
        owner_tag="Alex",
        name="alex",
        endpoint="https://alex-kimi-n123.openai.azure.com/",
    )
    row.__dict__.update(overrides)
    return row


def _sp(**overrides):
    row = SimpleNamespace(
        subscription_id="sub-1",
        elevated_access=True,
        tenant_id="tid",
        client_id="cid",
        name="alex",
        account_holder="alex@example.com",
        owner_tag="Alex",
        subscription_name="PayAsYouGo",
    )
    row.__dict__.update(overrides)
    return row


class Eligibility(unittest.TestCase):
    def test_k3_by_channel_or_resource(self):
        self.assertTrue(is_kimi_k3_account(_portal()))
        self.assertTrue(
            is_kimi_k3_account(_portal(resource_group="rg-old", resource_name="plain", new_api_name="kimi-k3-500k-proxy-1"))
        )
        self.assertFalse(
            is_kimi_k3_account(_portal(resource_group="rg-old", resource_name="gpt-prod", new_api_name="azure-old"))
        )

    def test_enabled_only(self):
        self.assertTrue(is_newapi_enabled(_portal()))
        self.assertFalse(is_newapi_enabled(_portal(new_api_status=2, new_api_status_o1=2)))
        self.assertFalse(is_newapi_enabled(_portal(new_api_status=1, new_api_status_o1=2)))
        self.assertFalse(is_newapi_enabled(_portal(new_api_gateway="O2", new_api_status=1, new_api_status_o1=None)))

    def test_live_status_must_be_enabled(self):
        self.assertTrue(is_live_newapi_enabled(KimiDeployResult(ok=True, new_api_status=1)))
        self.assertFalse(is_live_newapi_enabled(KimiDeployResult(ok=True, new_api_status=2)))
        self.assertFalse(is_live_newapi_enabled(KimiDeployResult(ok=True, new_api_status=None)))

    def test_live_status_falls_back_to_portal_when_pool_missing(self):
        missing = KimiDeployResult(ok=True, new_api_status=None)
        self.assertTrue(is_live_newapi_enabled(missing, _portal()))
        self.assertFalse(is_live_newapi_enabled(missing, _portal(new_api_status=2, new_api_status_o1=2)))
        self.assertFalse(is_live_newapi_enabled(KimiDeployResult(ok=True, new_api_status=2), _portal()))

    def test_scale_uses_portal_when_newapi_pool_missing(self):
        candidate = AutoscaleCandidate({}, _portal(), _sp())
        ready = KimiDeployResult(ok=True, tpm_upgrade_available=True, new_api_status=None)
        disabled = KimiDeployResult(ok=True, tpm_upgrade_available=True, new_api_status=2)
        self.assertTrue(_should_scale(candidate, ready))
        self.assertFalse(_should_scale(candidate, disabled))
        self.assertFalse(
            _should_scale(candidate, KimiDeployResult(ok=True, tpm_upgrade_available=False, new_api_status=1))
        )

    def test_select_skips_old_disabled_and_unelevated(self):
        old = _portal(subscription_id="old", resource_group="rg-legacy", resource_name="legacy", new_api_name="old-channel")
        disabled = _portal(subscription_id="sub-2", new_api_status=2, new_api_status_o1=2)
        unelevated = _portal(subscription_id="sub-3")
        ok = _portal()
        picked = select_autoscale_targets(
            [old, disabled, unelevated, ok],
            [_sp(), _sp(subscription_id="sub-2"), _sp(subscription_id="sub-3", elevated_access=False)],
        )
        self.assertEqual([row.portal.subscription_id for row in picked], ["sub-1"])

    def test_two_k3_resources_same_subscription(self):
        first = _portal(resource_name="alex-kimi-one")
        second = _portal(resource_name="alex-kimi-two", name="alex-2")
        picked = select_autoscale_targets([first, second], [_sp()])
        self.assertEqual([row.portal.resource_name for row in picked], ["alex-kimi-one", "alex-kimi-two"])


class NewApiWeightAfterUpgrade(unittest.TestCase):
    def test_only_default_weight_is_raised(self):
        self.assertEqual(UPGRADED_NEWAPI_WEIGHT, 4)
        self.assertEqual(TWO_MILLION_TPM, 2_000_000)
        self.assertTrue(needs_upgraded_weight(1))
        self.assertTrue(needs_upgraded_weight(None))
        self.assertFalse(needs_upgraded_weight(4))
        self.assertFalse(needs_upgraded_weight(10))

    def test_2m_gate(self):
        self.assertFalse(reached_2m_tpm(KimiDeployResult(ok=True, tpm=500_000, rpm=500, capacity=500)))
        self.assertTrue(reached_2m_tpm(KimiDeployResult(ok=True, tpm=2_000_000, rpm=2_000, capacity=2_000)))
        self.assertTrue(reached_2m_tpm(KimiDeployResult(ok=True, tpm=None, capacity=2_000)))

    def test_detail_includes_weight_when_it_changes(self):
        before = KimiDeployResult(ok=True, tpm=500_000, rpm=500)
        after = KimiDeployResult(ok=True, tpm=2_000_000, rpm=2_000)
        self.assertEqual(_detail(before, after), "TPM 500k → 2000k · RPM 500 → 2k")
        self.assertEqual(
            _detail(before, after, weight_before=1, weight_after=4),
            "TPM 500k → 2000k · RPM 500 → 2k · weight 1 → 4",
        )
        self.assertEqual(_detail(before, after, weight_before=4, weight_after=4), "TPM 500k → 2000k · RPM 500 → 2k")


class NewApiWeightAfterUpgradeAsync(unittest.IsolatedAsyncioTestCase):
    async def test_raises_weight_only_after_2m_tpm(self):
        candidate = AutoscaleCandidate({}, _portal(new_api_weight=1, new_api_channel_id=9), _sp())
        before = KimiDeployResult(ok=True, tpm=500_000, rpm=500, capacity=500)
        after = KimiDeployResult(
            ok=True,
            tpm=2_000_000,
            rpm=2_000,
            capacity=2_000,
            new_api_weight=1,
            new_api_channel_id=9,
            account_name="alex-kimi-n123",
            subscription_id="sub-1",
        )
        routed = KimiDeployResult(ok=True, new_api_weight=4, new_api_channel_id=9)
        with patch(
            "app.services.kimi_newapi.rename_kimi_newapi_channel",
            new=AsyncMock(return_value=routed),
        ) as rename:
            changes = await _raise_newapi_weights(SimpleNamespace(), [(candidate, before)], [after])
        rename.assert_awaited_once()
        self.assertEqual(rename.await_args.kwargs["weight"], 4)
        self.assertIs(rename.await_args.kwargs["sync_sheet"], False)
        self.assertEqual(after.new_api_weight, 4)
        self.assertEqual(changes[0], (1, 4, None))

    async def test_500k_tpm_rise_does_not_raise_weight(self):
        candidate = AutoscaleCandidate({}, _portal(new_api_weight=1, new_api_channel_id=9), _sp())
        before = KimiDeployResult(ok=True, tpm=25_000, rpm=25, capacity=25)
        after = KimiDeployResult(
            ok=True,
            tpm=500_000,
            rpm=500,
            capacity=500,
            new_api_weight=1,
            new_api_channel_id=9,
        )
        with patch("app.services.kimi_newapi.rename_kimi_newapi_channel", new=AsyncMock()) as rename:
            changes = await _raise_newapi_weights(SimpleNamespace(), [(candidate, before)], [after])
        rename.assert_not_awaited()
        self.assertEqual(changes, {})

    async def test_skips_weight_already_raised(self):
        candidate = AutoscaleCandidate({}, _portal(new_api_weight=4), _sp())
        before = KimiDeployResult(ok=True, tpm=500_000, rpm=500, capacity=500)
        after = KimiDeployResult(ok=True, tpm=2_000_000, rpm=2_000, capacity=2_000, new_api_weight=4)
        with patch("app.services.kimi_newapi.rename_kimi_newapi_channel", new=AsyncMock()) as rename:
            changes = await _raise_newapi_weights(SimpleNamespace(), [(candidate, before)], [after])
        rename.assert_not_awaited()
        self.assertEqual(changes, {})


class AutoQuotaPass(unittest.IsolatedAsyncioTestCase):
    async def test_already_at_max_tpm_does_not_reweight(self):
        candidate = AutoscaleCandidate({}, _portal(new_api_weight=1, new_api_channel_id=9), _sp())
        inventory = KimiDeployResult(
            ok=True,
            name="alex",
            tpm=500_000,
            rpm=500,
            capacity=500,
            quota_limit=500,
            tpm_upgrade_available=False,
            new_api_status=1,
            new_api_weight=1,
            new_api_channel_id=9,
            account_name="alex-kimi-n123",
            subscription_id="sub-1",
        )
        session = AsyncMock()
        session.commit = AsyncMock()
        with (
            patch("app.services.quota_autoscale.scale_accounts", new_callable=AsyncMock) as scale,
            patch("app.services.kimi_newapi.rename_kimi_newapi_channel", new_callable=AsyncMock) as rename,
            patch("app.services.quota_autoscale.add_log", new_callable=AsyncMock) as add_log,
            patch("app.services.quota_autoscale.prune", new_callable=AsyncMock),
        ):
            result = await _apply_auto_quota(session, [candidate], [inventory])
        scale.assert_not_awaited()
        rename.assert_not_awaited()
        add_log.assert_not_awaited()
        self.assertEqual(result, {"checked": 1, "upgraded": 0, "failed": 0})
        self.assertEqual(inventory.new_api_weight, 1)

    async def test_500k_tpm_rise_logs_without_changing_weight(self):
        candidate = AutoscaleCandidate({}, _portal(new_api_weight=1), _sp())
        inventory = KimiDeployResult(
            ok=True,
            name="alex",
            tpm=25_000,
            rpm=25,
            capacity=25,
            quota_limit=500,
            tpm_upgrade_available=True,
            new_api_status=1,
            new_api_weight=1,
        )
        scaled = KimiDeployResult(
            ok=True,
            name="alex",
            tpm=500_000,
            rpm=500,
            capacity=500,
            quota_limit=500,
            tpm_upgrade_available=False,
            new_api_status=1,
            new_api_weight=1,
        )
        session = AsyncMock()
        session.commit = AsyncMock()
        with (
            patch("app.services.quota_autoscale.scale_accounts", new_callable=AsyncMock, return_value=[scaled]) as scale,
            patch("app.services.kimi_newapi.rename_kimi_newapi_channel", new_callable=AsyncMock) as rename,
            patch("app.services.quota_autoscale.add_log", new_callable=AsyncMock) as add_log,
            patch("app.services.quota_autoscale.prune", new_callable=AsyncMock),
        ):
            result = await _apply_auto_quota(session, [candidate], [inventory])
        scale.assert_awaited_once()
        rename.assert_not_awaited()
        self.assertEqual(result, {"checked": 1, "upgraded": 1, "failed": 0})
        self.assertEqual(scaled.new_api_weight, 1)
        self.assertEqual(add_log.await_args.kwargs["status"], "ok")
        self.assertEqual(add_log.await_args.kwargs["detail"], "TPM 25k → 500k · RPM 25 → 500")
        self.assertNotIn("weight", add_log.await_args.kwargs["detail"])

    async def test_unknown_quota_does_not_scale_or_reweight(self):
        candidate = AutoscaleCandidate({}, _portal(), _sp())
        inventory = KimiDeployResult(
            ok=True,
            tpm=25_000,
            rpm=25,
            capacity=25,
            quota_limit=None,
            tpm_upgrade_available=False,
            new_api_status=1,
            new_api_weight=1,
        )
        session = AsyncMock()
        session.commit = AsyncMock()
        with (
            patch("app.services.quota_autoscale.scale_accounts", new_callable=AsyncMock) as scale,
            patch("app.services.kimi_newapi.rename_kimi_newapi_channel", new=AsyncMock()) as rename,
            patch("app.services.quota_autoscale.add_log", new_callable=AsyncMock) as add_log,
            patch("app.services.quota_autoscale.prune", new_callable=AsyncMock),
        ):
            result = await _apply_auto_quota(session, [candidate], [inventory])
        scale.assert_not_awaited()
        rename.assert_not_awaited()
        add_log.assert_not_awaited()
        self.assertEqual(result, {"checked": 1, "upgraded": 0, "failed": 0})


if __name__ == "__main__":
    unittest.main()
