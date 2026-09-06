import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.join_enrollee_service import EnrolleeError, normalize_auto_approve, normalize_enrollee_name
from app.services.submit_service import _ensure_approve_newapi, _row_can_auto_retry
from app.services.telegram_service import format_k3_deployed_notice


class JoinEnrolleeNames(unittest.TestCase):
    def test_canonicalizes_name(self):
        self.assertEqual(normalize_enrollee_name("  snig "), "Snig")
        self.assertEqual(normalize_enrollee_name("ritesH"), "Ritesh")

    def test_rejects_blank(self):
        with self.assertRaises(EnrolleeError):
            normalize_enrollee_name("")
        with self.assertRaises(EnrolleeError):
            normalize_enrollee_name("   ")


class JoinAutoApproveSetting(unittest.TestCase):
    def test_default_off(self):
        self.assertFalse(normalize_auto_approve(None))
        self.assertFalse(normalize_auto_approve({}))
        self.assertFalse(normalize_auto_approve({"enabled": False}))

    def test_enabled_values(self):
        self.assertTrue(normalize_auto_approve({"enabled": True}))
        self.assertTrue(normalize_auto_approve({"enabled": "true"}))
        self.assertTrue(normalize_auto_approve({"enabled": 1}))

    def test_rejects_other_truthy(self):
        self.assertFalse(normalize_auto_approve({"enabled": "maybe"}))
        self.assertFalse(normalize_auto_approve({"enabled": 2}))


class AutoRetryGate(unittest.TestCase):
    def test_only_failed_deploy_with_identity(self):
        self.assertFalse(_row_can_auto_retry(None))
        self.assertFalse(
            _row_can_auto_retry(
                SimpleNamespace(
                    status="failed",
                    error_kind="deploy",
                    client_secret_encrypted=None,
                    client_id="id",
                    subscription_id="sub",
                    tenant_id="tid",
                )
            )
        )
        self.assertFalse(
            _row_can_auto_retry(
                SimpleNamespace(
                    status="failed",
                    error_kind="roles",
                    client_secret_encrypted="x",
                    client_id="id",
                    subscription_id="sub",
                    tenant_id="tid",
                )
            )
        )
        self.assertTrue(
            _row_can_auto_retry(
                SimpleNamespace(
                    status="failed",
                    error_kind="deploy",
                    client_secret_encrypted="x",
                    client_id="id",
                    subscription_id="sub",
                    tenant_id="tid",
                    auto_retry_count=5,
                )
            )
        )
        self.assertFalse(
            _row_can_auto_retry(
                SimpleNamespace(
                    status="failed",
                    error_kind="deploy",
                    client_secret_encrypted="x",
                    client_id="id",
                    subscription_id="sub",
                    tenant_id="tid",
                    auto_retry_count=6,
                )
            )
        )


class ApproveNewApiRequired(unittest.IsolatedAsyncioTestCase):
    async def test_leaves_attached_channel_alone(self):
        result = SimpleNamespace(ok=True, new_api_present=True, new_api_error=None, error=None)
        out = await _ensure_approve_newapi(None, {}, [result], 10, 1)
        self.assertTrue(out[0].ok)
        self.assertTrue(out[0].new_api_present)

    async def test_missing_channel_fails_when_newapi_is_configured(self):
        result = SimpleNamespace(
            ok=True,
            new_api_present=False,
            new_api_error="No stored Foundry API key. Deploy or test the model first.",
            error=None,
            account_name="snig-10k-2",
            azure_openai_endpoint=None,
            resource_group=None,
            deployment_name="FW-Kimi-K3",
        )
        with (
            patch("app.services.submit_service._newapi_configured", return_value=True),
            patch(
                "app.services.kimi_deploy_service.add_kimi_newapi_channels",
                new=AsyncMock(return_value=[result]),
            ),
        ):
            out = await _ensure_approve_newapi(None, {"AZURE_SUBSCRIPTION_ID": "sub"}, [result], 10, 1)
        self.assertFalse(out[0].ok)
        self.assertIn("Foundry API key", out[0].error)

    async def test_skips_when_newapi_is_not_configured(self):
        result = SimpleNamespace(ok=True, new_api_present=False, new_api_error=None, error=None)
        with patch("app.services.submit_service._newapi_configured", return_value=False):
            out = await _ensure_approve_newapi(None, {}, [result], 10, 1)
        self.assertTrue(out[0].ok)


class K3DeployNotice(unittest.TestCase):
    def test_auto_approve_copy(self):
        self.assertEqual(
            format_k3_deployed_notice("Snig", "snig-10k-2", "auto-approve"),
            "<b>K3 deployed</b>\nSnig · <code>snig-10k-2</code>\nAuthorized: auto-approve",
        )

    def test_admin_copy(self):
        self.assertEqual(
            format_k3_deployed_notice("Gaurav", "gaurav-1k-1", "admin"),
            "<b>K3 deployed</b>\nGaurav · <code>gaurav-1k-1</code>\nAuthorized: admin",
        )

    def test_escapes_html(self):
        text = format_k3_deployed_notice("<script>", "a&b", "admin")
        self.assertIn("&lt;script&gt;", text)
        self.assertIn("a&amp;b", text)
        self.assertNotIn("<script>", text)


if __name__ == "__main__":
    unittest.main()
