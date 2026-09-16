import importlib.util
import unittest
from pathlib import Path


def _load_deploy():
    here = Path(__file__).resolve()
    candidates = [
        here.parents[1] / "scripts" / "kimi_k3_deploy.py",
        here.parents[2] / "scripts" / "kimi_k3_deploy.py",
        Path("/app/scripts/kimi_k3_deploy.py"),
    ]
    path = next((item for item in candidates if item.is_file()), candidates[0])
    spec = importlib.util.spec_from_file_location("kimi_k3_deploy_purge", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PurgeProjectNames(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_deploy()

    def test_default_project_from_resource_group(self):
        self.assertEqual(self.mod.stack_project_name("rg-anirudh-proxy"), "anirudh-proxy")
        self.assertEqual(self.mod.stack_project_name("rg-alex-kimi"), "alex-kimi")
        self.assertEqual(self.mod.stack_project_name(""), "")

    def test_names_from_list_and_error(self):
        listed = self.mod.project_names_from_list(
            [
                {"name": "anirudh-proxy"},
                {"id": "/subscriptions/x/resourceGroups/rg-anirudh-proxy/providers/Microsoft.CognitiveServices/accounts/anirudh-proxy-o2bwni/projects/extra"},
            ]
        )
        self.assertEqual(listed, ["anirudh-proxy", "extra"])
        nested = self.mod.project_names_from_error(
            "Cannot delete resource while nested resources exist. "
            "Some existing nested resource IDs include: "
            "'Microsoft.CognitiveServices/accounts/anirudh-proxy-o2bwni/projects/anirudh-proxy'."
        )
        self.assertEqual(nested, ["anirudh-proxy"])


if __name__ == "__main__":
    unittest.main()
