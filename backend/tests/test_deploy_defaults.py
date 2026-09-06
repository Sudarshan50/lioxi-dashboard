import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services.deploy_defaults import DEFAULT_PRIORITY, DEFAULT_WEIGHT, normalize_deploy_defaults
from app.services.kimi_deploy_service import _safe_deploy


class DeployDefaults(unittest.TestCase):
    def test_factory_default_is_ten_one(self):
        self.assertEqual(normalize_deploy_defaults(), {"priority": 10, "weight": 1})
        self.assertEqual(DEFAULT_PRIORITY, 10)
        self.assertEqual(DEFAULT_WEIGHT, 1)

    def test_accepts_saved_values(self):
        self.assertEqual(normalize_deploy_defaults({"priority": 7, "weight": 3}), {"priority": 7, "weight": 3})

    def test_rejects_out_of_range(self):
        with self.assertRaises(ValueError):
            normalize_deploy_defaults({"priority": -1, "weight": 1})
        with self.assertRaises(ValueError):
            normalize_deploy_defaults({"priority": 10, "weight": 0})


class SafeDeployRetry(unittest.TestCase):
    def test_retries_once_then_succeeds(self):
        calls = {"n": 0}

        def deploy_one(_account):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("temporary Azure blip")
            return {"ok": True, "account_name": "acct"}

        with patch("app.services.kimi_deploy_service.time.sleep"):
            result = _safe_deploy(SimpleNamespace(deploy_one=deploy_one), {"name": "acct"})
        self.assertEqual(calls["n"], 2)
        self.assertTrue(result["ok"])
        self.assertEqual(result["account_name"], "acct")

    def test_shows_failure_after_two_attempts(self):
        calls = {"n": 0}

        def deploy_one(_account):
            calls["n"] += 1
            raise RuntimeError("still broken")

        with patch("app.services.kimi_deploy_service.time.sleep"):
            result = _safe_deploy(SimpleNamespace(deploy_one=deploy_one), {"name": "acct"})
        self.assertEqual(calls["n"], 2)
        self.assertFalse(result["ok"])
        self.assertIn("still broken", result["error"])
