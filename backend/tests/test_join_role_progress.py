"""Role-assignment progress events that drive the Join progress bar."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services import submit_service


class _Az:
    def __init__(self):
        self.assigned = []

    def set_log(self, _emit):
        return None

    async def set_subscription(self, _sub):
        return None

    async def sp_object_id(self, _app_id):
        return "oid-1"

    async def add_sp_as_app_owner(self, _app_id, _oid):
        return None

    async def assign_role(self, _app_id, role, _sub, object_id=None, timeout=None):
        self.assigned.append(role)
        return True, None

    async def assign_billing_reader(self, _oid, _tenant):
        return True, None


class _Box:
    def decrypt(self, value):
        return "secret"

    def encrypt(self, value):
        return "enc"


def _row():
    return SimpleNamespace(
        session_id="sess-1",
        az_config_dir=None,
        subscription_id="sub-1",
        tenant_id="tenant-1",
        subscription_name="Pay-As-You-Go",
        client_id="app-1",
        client_secret_encrypted="enc",
        sp_display_name="monitor",
        billing_error=None,
        error_message=None,
        status=submit_service.STATUS_CREATING_SP,
    )


class RoleProgressEvents(unittest.IsolatedAsyncioTestCase):
    async def _run(self, roles, admin_roles):
        az = _Az()
        db = SimpleNamespace(
            execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None)),
            commit=AsyncMock(),
            refresh=AsyncMock(),
        )
        events = []

        async def emit(event):
            events.append(event)

        with (
            patch.object(submit_service, "get_az_session", new=AsyncMock(return_value=az)),
            patch.object(submit_service, "is_tenant_level_account", return_value=False),
            patch.object(submit_service, "get_secret_box", return_value=_Box()),
            patch.object(submit_service, "_verify_sp_secret", new=AsyncMock(return_value="ok")),
            patch.object(submit_service, "_ensure_still_creating", new=AsyncMock()),
            patch.object(submit_service, "session_aborted", return_value=False),
            patch.object(submit_service, "drop_az_session", new=AsyncMock()),
            patch.object(
                submit_service,
                "load_deploy_module",
                return_value=SimpleNamespace(ALL_ROLES=roles, ADMIN_ROLES=admin_roles),
            ),
        ):
            await submit_service._provision_sp(db, _row(), "slug", emit)
        return az, [event for event in events if "total" in event]

    async def test_total_counts_identity_every_role_and_billing(self):
        _, progress = await self._run(["Reader", "Contributor"], ["Contributor"])
        self.assertTrue(progress)
        self.assertEqual({event["total"] for event in progress}, {4})

    async def test_bar_starts_at_zero_before_any_azure_call(self):
        _, progress = await self._run(["Reader"], [])
        self.assertEqual(progress[0]["done"], 0)
        self.assertIn("monitor identity", progress[0]["message"].lower())

    async def test_identity_is_the_first_completed_step(self):
        _, progress = await self._run(["Reader"], [])
        identity = next(event for event in progress if event["done"] == 1)
        self.assertNotIn("role", identity)

    async def test_progress_never_moves_backwards_and_finishes_full(self):
        _, progress = await self._run(["Reader", "Monitoring Reader", "Contributor"], ["Contributor"])
        done = [event["done"] for event in progress]
        self.assertEqual(done[0], 0)
        self.assertEqual(done, sorted(done))
        self.assertEqual(done[-1], progress[-1]["total"])

    async def test_each_role_reports_start_and_finish_after_identity(self):
        az, progress = await self._run(["Reader", "Contributor"], ["Contributor"])
        pairs = [(event["role"], event["done"]) for event in progress if "role" in event]
        self.assertEqual(az.assigned, ["Contributor", "Reader"])
        self.assertEqual(pairs, [("Contributor", 1), ("Contributor", 2), ("Reader", 2), ("Reader", 3)])

    async def test_billing_step_completes_the_bar(self):
        _, progress = await self._run(["Reader"], [])
        billing = [event for event in progress if "billing reader" in event["message"]]
        self.assertEqual([event["done"] for event in billing], [2])
        self.assertEqual(progress[-1]["done"], progress[-1]["total"])


if __name__ == "__main__":
    unittest.main()
