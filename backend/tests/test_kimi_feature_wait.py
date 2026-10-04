import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


def _load_deploy():
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / "scripts" / "kimi_k3_deploy.py",
        here.parents[1] / "scripts" / "kimi_k3_deploy.py",
        Path("/app/scripts/kimi_k3_deploy.py"),
    ]
    path = next((item for item in candidates if item.is_file()), candidates[0])
    spec = importlib.util.spec_from_file_location("kimi_k3_deploy_feature_wait", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FireworksQuotaWait(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_deploy()

    def _clock(self):
        now = {"t": 1000.0}

        def fake_time():
            return now["t"]

        def fake_sleep(seconds):
            now["t"] += seconds

        return fake_time, fake_sleep

    def test_returns_immediately_when_quota_is_already_open(self):
        mod = self.mod
        fake_time, fake_sleep = self._clock()
        with (
            patch.object(mod, "fireworks_feature_state", return_value="Registered"),
            patch.object(mod, "fireworks_quota", return_value=(0, 40)),
            patch.object(mod, "register_fireworks_feature") as register,
            patch.object(mod, "progress"),
            patch.object(mod.time, "time", fake_time),
            patch.object(mod.time, "sleep", fake_sleep),
        ):
            current, limit = mod.wait_for_fireworks_quota(None, "eastus2", timeout=30, interval=10)
        self.assertEqual((current, limit), (0, 40))
        register.assert_not_called()

    def test_waits_through_a_zero_reading_then_returns_quota(self):
        mod = self.mod
        fake_time, fake_sleep = self._clock()
        quotas = [(0, 0), (2, 80)]
        with (
            patch.object(mod, "fireworks_feature_state", return_value="Registering"),
            patch.object(mod, "fireworks_quota", side_effect=quotas),
            patch.object(mod, "register_fireworks_feature") as register,
            patch.object(mod, "progress"),
            patch.object(mod.time, "time", fake_time),
            patch.object(mod.time, "sleep", fake_sleep),
        ):
            current, limit = mod.wait_for_fireworks_quota(None, "eastus2", timeout=30, interval=10)
        self.assertEqual((current, limit), (2, 80))
        register.assert_not_called()

    def test_registered_feature_with_zero_quota_is_a_permanent_block(self):
        mod = self.mod
        fake_time, fake_sleep = self._clock()
        with (
            patch.object(mod, "fireworks_feature_state", return_value="Registered"),
            patch.object(mod, "fireworks_quota", return_value=(0, 0)),
            patch.object(mod, "progress"),
            patch.object(mod.time, "time", fake_time),
            patch.object(mod.time, "sleep", fake_sleep),
        ):
            with self.assertRaises(mod.AzError) as caught:
                mod.wait_for_fireworks_quota(None, "eastus2", timeout=20, interval=10)
        self.assertIn("SpecialFeatureOrQuotaIdRequired", str(caught.exception))
        self.assertTrue(mod.model_unavailable(str(caught.exception)))

    def test_still_registering_at_the_deadline_stays_retryable(self):
        mod = self.mod
        fake_time, fake_sleep = self._clock()
        with (
            patch.object(mod, "fireworks_feature_state", return_value="Registering"),
            patch.object(mod, "fireworks_quota", return_value=(0, 0)),
            patch.object(mod, "progress"),
            patch.object(mod.time, "time", fake_time),
            patch.object(mod.time, "sleep", fake_sleep),
        ):
            with self.assertRaises(mod.AzError) as caught:
                mod.wait_for_fireworks_quota(None, "eastus2", timeout=20, interval=10)
        text = str(caught.exception)
        self.assertIn("Retry deploy", text)
        self.assertFalse(mod.model_unavailable(text))
