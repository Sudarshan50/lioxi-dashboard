import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.submit_service import SubmitError, reject_many, reject_request


def _row(**kwargs):
    defaults = dict(
        id=1,
        status="failed",
        error_kind="roles",
        group_tag="sb",
        session_id="sid-1",
        subscription_id="sub-1",
        tenant_id="tid-1",
        client_id="cid-1",
        client_secret_encrypted="enc",
        subscription_name="Contoso",
        name="Alex",
        account_holder="alex@example.com",
        person_associated="Alex",
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _db(rows=None):
    session = AsyncMock()
    session.execute = AsyncMock(return_value=SimpleNamespace(scalars=lambda: list(rows or [])))
    session.delete = AsyncMock()
    session.commit = AsyncMock()
    return session


def _common_patches():
    stack = ExitStack()
    stack.enter_context(patch("app.services.submit_service.abort_approve_work", new_callable=AsyncMock))
    stack.enter_context(patch("app.services.submit_service.abort_session_work", new_callable=AsyncMock))
    stack.enter_context(patch("app.services.submit_service.drop_az_session", new_callable=AsyncMock))
    stack.enter_context(patch("app.services.submit_service._publish", new_callable=AsyncMock))
    stack.enter_context(
        patch("app.services.service_principal_store.drop_orphan_service_principal", new_callable=AsyncMock)
    )
    stack.enter_context(
        patch(
            "app.services.submit_service.get_secret_box",
            return_value=SimpleNamespace(decrypt=lambda _secret: "plain-secret"),
        )
    )
    return stack


class RejectMany(unittest.IsolatedAsyncioTestCase):
    async def test_no_ids_sweeps_failed_only(self):
        failed = _row(id=11, error_kind="roles")
        waiting = _row(id=12, status="pending_approval", error_kind=None)
        db = _db()
        with _common_patches():
            with patch("app.services.submit_service.list_pending", new=AsyncMock(return_value=[failed, waiting])):
                with patch("app.services.kimi_deploy_service.delete_accounts", new_callable=AsyncMock) as delete:
                    declined, skipped = await reject_many(db, None)
        self.assertEqual(declined, [11])
        self.assertEqual(skipped, [])
        delete.assert_awaited()
        db.delete.assert_awaited_once_with(failed)
        db.commit.assert_awaited_once()

    async def test_declines_roles_even_when_leftover_delete_fails(self):
        row = _row(id=21, error_kind="roles")
        db = _db([row])
        failed = SimpleNamespace(ok=False, error="429 Too Many Requests")
        with _common_patches():
            with patch(
                "app.services.kimi_deploy_service.delete_accounts",
                new_callable=AsyncMock,
                return_value=[failed],
            ):
                declined, skipped = await reject_many(db, [21])
        self.assertEqual(declined, [21])
        self.assertEqual(skipped, [])
        db.delete.assert_awaited_once_with(row)

    async def test_keeps_deploy_card_when_leftover_delete_fails(self):
        row = _row(id=31, error_kind="deploy")
        db = _db([row])
        failed = SimpleNamespace(ok=False, error="stack still busy")
        with _common_patches():
            with patch(
                "app.services.kimi_deploy_service.delete_accounts",
                new_callable=AsyncMock,
                return_value=[failed],
            ):
                declined, skipped = await reject_many(db, [31])
        self.assertEqual(declined, [])
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0][0], 31)
        self.assertIn("Clear leftover", skipped[0][1])
        db.delete.assert_not_awaited()

    async def test_batches_leftover_deletes(self):
        deploy = _row(id=41, error_kind="deploy")
        roles = _row(id=42, error_kind="roles", session_id="sid-2", subscription_id="sub-2")
        db = _db([deploy, roles])
        ok = SimpleNamespace(ok=True, error=None)
        with _common_patches():
            with patch(
                "app.services.kimi_deploy_service.delete_accounts",
                new_callable=AsyncMock,
                return_value=[ok, ok],
            ) as delete:
                declined, skipped = await reject_many(db, [41, 42])
        self.assertEqual(declined, [41, 42])
        self.assertEqual(skipped, [])
        self.assertEqual(delete.await_count, 1)
        payloads = delete.await_args.args[0]
        self.assertEqual(len(payloads), 2)

    async def test_missing_subscription_leftover_still_drops_deploy_card(self):
        row = _row(id=51, error_kind="deploy")
        db = _db([row])
        failed = SimpleNamespace(ok=False, error="SubscriptionNotFound: subscription could not be found")
        with _common_patches():
            with patch(
                "app.services.kimi_deploy_service.delete_accounts",
                new_callable=AsyncMock,
                return_value=[failed],
            ):
                declined, skipped = await reject_many(db, [51])
        self.assertEqual(declined, [51])
        self.assertEqual(skipped, [])
        db.delete.assert_awaited_once_with(row)

    async def test_group_filter_skips_other_group(self):
        sb = _row(id=61, group_tag="sb")
        vcs = _row(id=62, group_tag="vcs", session_id="sid-vcs")
        db = _db([sb, vcs])
        with _common_patches():
            with patch(
                "app.services.kimi_deploy_service.delete_accounts",
                new_callable=AsyncMock,
                return_value=[],
            ):
                declined, skipped = await reject_many(db, [61, 62], group="sb")
        self.assertEqual(declined, [61])
        self.assertEqual(skipped, [])
        db.delete.assert_awaited_once_with(sb)

    async def test_skips_approving_and_unknown_ids(self):
        live = _row(id=71, status="approving", error_kind=None)
        db = _db([live])
        with _common_patches():
            declined, skipped = await reject_many(db, [71, 99])
        self.assertEqual(declined, [])
        self.assertEqual({item[0] for item in skipped}, {71, 99})
        db.delete.assert_not_awaited()

    async def test_skips_azure_cleanup_for_tenant_login(self):
        row = _row(id=81, error_kind="account", subscription_id="tid-1", tenant_id="tid-1")
        db = _db([row])
        with _common_patches():
            with patch("app.services.kimi_deploy_service.delete_accounts", new_callable=AsyncMock) as delete:
                declined, skipped = await reject_many(db, [81])
        self.assertEqual(declined, [81])
        self.assertEqual(skipped, [])
        delete.assert_not_awaited()


class RejectRequest(unittest.IsolatedAsyncioTestCase):
    async def test_leftover_failure_keeps_deploy_card(self):
        row = _row(id=91, error_kind="deploy")
        db = _db()
        failed = SimpleNamespace(ok=False, error="quota still locked")
        with _common_patches():
            with patch("app.services.submit_service.get_request_by_id", new=AsyncMock(return_value=row)):
                with patch(
                    "app.services.kimi_deploy_service.delete_accounts",
                    new_callable=AsyncMock,
                    return_value=[failed],
                ):
                    with self.assertRaises(SubmitError) as ctx:
                        await reject_request(db, 91)
        self.assertIn("Clear leftover", str(ctx.exception))
        db.delete.assert_not_awaited()
        db.commit.assert_not_awaited()
