import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.account_service import (
    AccountService,
    AccountValidationError,
    azure_resource_missing,
    azure_stack_already_gone,
    portal_account_for_redeploy,
)
from app.services.kimi_deploy_service import KimiDeployError


def _account(**overrides):
    base = dict(
        id=226,
        name="Mayank",
        provider_type="azure_openai",
        tenant_id="tenant",
        client_id="client",
        client_secret_encrypted="enc",
        subscription_id="51cf2972-cbe0-49b7-a100-6560a40960f2",
        resource_name="mayank-proxy-mcpfb1",
        resource_group="rg-mayank-proxy",
        resource_id="/subscriptions/x/resourceGroups/rg-mayank-proxy/providers/Microsoft.CognitiveServices/accounts/mayank-proxy-mcpfb1",
        endpoint="https://mayank.example",
        new_api_name="cs-proxy-1",
        new_api_priority=10,
        new_api_weight=1,
        new_api_channel_id=None,
        owner_tag="Mayank",
        group_tag="vcs",
        last_sync_error="Azure API error (404): Resource group 'rg-mayank-proxy' could not be found.",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class AzureAlreadyGone(unittest.TestCase):
    def test_resource_group_404(self):
        self.assertTrue(
            azure_stack_already_gone("Azure API error (404): Resource group 'rg-mayank-proxy' could not be found.")
        )

    def test_cognitive_account_was_not_found(self):
        err = (
            "Azure API error (404): The Resource 'Microsoft.CognitiveServices/accounts/lioxishaurya8-kimi-t6vw3v' "
            "under resource group 'rg-lioxishaurya8-kimi' was not found."
        )
        self.assertTrue(azure_resource_missing(err))
        self.assertTrue(azure_stack_already_gone(err))

    def test_live_failure_is_not_gone(self):
        self.assertFalse(azure_stack_already_gone("429 Too Many Requests"))
        self.assertFalse(azure_resource_missing("429 Too Many Requests"))


class PortalAccountForRedeploy(unittest.TestCase):
    def test_reuses_same_name_on_subscription(self):
        keep = SimpleNamespace(name="Lioxi-Shaurya8", resource_name="old-stack")
        other = SimpleNamespace(name="Lioxi-Shaurya7", resource_name="other")
        self.assertIs(portal_account_for_redeploy([keep, other], "Lioxi-Shaurya8"), keep)

    def test_single_sibling_when_name_missing(self):
        only = SimpleNamespace(name="Lioxi-Shaurya8", resource_name="old-stack")
        self.assertIs(portal_account_for_redeploy([only], ""), only)

    def test_ambiguous_siblings_without_name(self):
        rows = [
            SimpleNamespace(name="A", resource_name="one"),
            SimpleNamespace(name="B", resource_name="two"),
        ]
        self.assertIsNone(portal_account_for_redeploy(rows, ""))


class DeleteAccountAlreadyGone(unittest.IsolatedAsyncioTestCase):
    def _service(self, account):
        repo = SimpleNamespace(
            _session=object(),
            get=AsyncMock(return_value=account),
            delete=AsyncMock(),
        )
        box = SimpleNamespace(decrypt=lambda _secret: "plain-secret")
        return AccountService(repo, box)

    async def test_portal_cleanup_when_undeploy_fails_on_missing_rg(self):
        account = _account()
        service = self._service(account)
        failed = SimpleNamespace(ok=False, error="Resource group 'rg-mayank-proxy' could not be found.")
        with (
            patch("app.services.kimi_deploy_service.delete_accounts", new_callable=AsyncMock, return_value=[failed]),
            patch("app.services.google_sheet_inventory.mark_deleted_inventory", new_callable=AsyncMock) as sheet,
            patch("app.services.openai_key_store.drop_foundry_key", new_callable=AsyncMock) as drop_key,
            patch("app.services.azure_inventory_cache.drop_azure_inventory", new_callable=AsyncMock),
            patch("app.services.service_principal_store.drop_stored_principal", new_callable=AsyncMock),
            patch(
                "app.services.submit_service.release_join_for_subscription",
                new_callable=AsyncMock,
            ) as release,
        ):
            await service.delete_account(226)
        sheet.assert_awaited_once()
        drop_key.assert_awaited_once()
        release.assert_awaited_once_with(
            service._account_repository._session, account.subscription_id, account.name
        )
        service._account_repository.delete.assert_awaited_once_with(account)

    async def test_undeploy_error_still_blocks_when_stack_is_live(self):
        account = _account(last_sync_error=None)
        service = self._service(account)
        failed = SimpleNamespace(ok=False, error="429 Too Many Requests")
        with patch("app.services.kimi_deploy_service.delete_accounts", new_callable=AsyncMock, return_value=[failed]):
            with self.assertRaises(AccountValidationError):
                await service.delete_account(226)
        service._account_repository.delete.assert_not_awaited()

    async def test_deploy_exception_skipped_when_last_sync_already_gone(self):
        account = _account()
        service = self._service(account)
        with (
            patch(
                "app.services.kimi_deploy_service.delete_accounts",
                new_callable=AsyncMock,
                side_effect=KimiDeployError("az login failed"),
            ),
            patch("app.services.google_sheet_inventory.mark_deleted_inventory", new_callable=AsyncMock),
            patch("app.services.openai_key_store.drop_foundry_key", new_callable=AsyncMock),
            patch("app.services.azure_inventory_cache.drop_azure_inventory", new_callable=AsyncMock),
            patch("app.services.service_principal_store.drop_stored_principal", new_callable=AsyncMock),
            patch("app.services.submit_service.release_join_for_subscription", new_callable=AsyncMock),
        ):
            await service.delete_account(226)
        service._account_repository.delete.assert_awaited_once_with(account)


class ReleaseJoinAfterDelete(unittest.IsolatedAsyncioTestCase):
    async def test_deletes_approved_row_and_email_only_leftovers(self):
        from app.services.submit_service import release_join_for_subscription

        approved = SimpleNamespace(
            id=348,
            session_id="sid-approved",
            subscription_id="51cf2972-cbe0-49b7-a100-6560a40960f2",
            account_holder="mayankgoel2005@gmail.com",
            name="Mayank",
        )
        expired = SimpleNamespace(
            id=501,
            session_id="sid-expired",
            subscription_id=None,
            account_holder="mayankgoel2005@gmail.com",
            name=None,
        )
        session = AsyncMock()
        session.execute = AsyncMock(
            side_effect=[
                SimpleNamespace(scalars=lambda: [approved]),
                SimpleNamespace(scalars=lambda: [expired]),
            ]
        )
        with (
            patch("app.services.submit_service.abort_approve_work", new_callable=AsyncMock) as abort_approve,
            patch("app.services.submit_service.abort_session_work", new_callable=AsyncMock) as abort_session,
        ):
            await release_join_for_subscription(
                session, "51CF2972-CBE0-49B7-A100-6560A40960F2", "Mayank6"
            )
        self.assertEqual([call.args[0] for call in session.delete.await_args_list], [approved, expired])
        session.commit.assert_awaited_once()
        abort_approve.assert_any_await(348)
        abort_approve.assert_any_await(501)
        abort_session.assert_any_await("sid-approved")
        abort_session.assert_any_await("sid-expired")

    async def test_discards_orphaned_approved_row_on_rejoin(self):
        from app.services.submit_service import _discard_stale_for_subscription

        leftover = SimpleNamespace(
            id=314,
            session_id="sid-anirudh",
            client_id="old-app",
            client_secret_encrypted="enc",
            sp_display_name="usage-and-credits-monitor",
        )
        incoming = SimpleNamespace(client_id=None, client_secret_encrypted=None, sp_display_name=None)
        session = AsyncMock()
        session.execute = AsyncMock(return_value=SimpleNamespace(scalars=lambda: [leftover]))
        with (
            patch("app.services.submit_service.abort_approve_work", new_callable=AsyncMock),
            patch("app.services.submit_service.abort_session_work", new_callable=AsyncMock),
        ):
            await _discard_stale_for_subscription(
                session, "46155d32-9b88-44aa-90c1-2e9eefa75414", exclude_id=99, inherit_into=incoming
            )
        session.delete.assert_awaited_once_with(leftover)
        self.assertEqual(incoming.client_id, "old-app")
        self.assertEqual(incoming.client_secret_encrypted, "enc")


class RedeployAccount(unittest.IsolatedAsyncioTestCase):
    def _service(self, account):
        session = AsyncMock()
        session.execute = AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(account_holder="x@y.com"))
        )
        repo = SimpleNamespace(_session=session, get=AsyncMock(return_value=account))
        box = SimpleNamespace(decrypt=lambda _secret: "plain-secret")
        return AccountService(repo, box)

    async def test_connection_ok_when_resource_404_but_sp_lists(self):
        account = _account()
        service = self._service(account)
        provider = SimpleNamespace(
            list_deployments=AsyncMock(
                side_effect=RuntimeError(
                    "Azure API error (404): The Resource 'Microsoft.CognitiveServices/accounts/x' was not found."
                )
            ),
            discover_resources=AsyncMock(return_value=[]),
        )
        with patch("app.services.account_service.get_provider", return_value=provider):
            result = await service.test_connection(226)
        self.assertEqual(result["status"], "ok")
        self.assertIn("missing", result["detail"].lower())

    async def test_queue_redeploy_starts_job(self):
        account = _account(new_api_name="kimi-k3-500k-proxy-186", new_api_channel_id=186)
        service = self._service(account)
        job = SimpleNamespace(job_id="abc")
        with (
            patch("app.services.deploy_defaults.resolve_routing", new_callable=AsyncMock, return_value=(10, 1)),
            patch("app.services.deploy_job_runner.start_kimi_deploy_job", new_callable=AsyncMock, return_value=job) as start,
        ):
            result = await service.queue_redeploy(226)
        self.assertEqual(result, {"status": "queued", "job_id": "abc", "name": "Mayank"})
        start.assert_awaited_once()
        payload = start.await_args.args[0][0]
        self.assertEqual(payload["new_api_name"], "kimi-k3-500k-proxy-186")
        self.assertEqual(payload["account_holder"], "x@y.com")
        self.assertEqual(payload["new_api_enable"], "1")
        self.assertTrue(start.await_args.kwargs.get("on_complete") or start.await_args.args[4:])

    async def test_finalize_redeploy_syncs_and_enables(self):
        account = _account(
            name="Lioxi-Shaurya8",
            resource_name="lioxishaurya8-kimi-w556kl",
            new_api_name="kimi-k3-500k-proxy-186",
            new_api_status=2,
            new_api_status_o1=2,
        )
        service = self._service(account)
        orchestrator = SimpleNamespace(sync_one=AsyncMock(return_value={"status": "success", "error": None}))
        account.last_sync_status = "success"
        account.last_sync_error = None
        account.new_api_status = 1
        account.new_api_status_o1 = 1
        with (
            patch.object(service, "_retarget_newapi_channel", new_callable=AsyncMock) as retarget,
            patch("app.services.new_api_service.set_gateway_status", new_callable=AsyncMock, return_value={"status": "ok"}) as enable,
            patch("app.dependencies.get_sync_orchestrator", return_value=orchestrator),
        ):
            summary = await service.finalize_redeploy(226)
        retarget.assert_awaited_once()
        enable.assert_awaited_once()
        orchestrator.sync_one.assert_awaited_once_with(226)
        self.assertEqual(summary["last_sync_status"], "success")
        self.assertEqual(summary["new_api_status"], 1)
