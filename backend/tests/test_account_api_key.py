import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.core.exceptions import AzureApiError
from app.services.account_service import AccountNotFoundError, AccountService, AccountValidationError


def _account(**overrides):
    base = dict(
        id=12,
        name="Mayank",
        tenant_id="tenant",
        client_id="client",
        client_secret_encrypted="enc",
        subscription_id="sub-1",
        resource_name="mayank-proxy",
        resource_group="rg-mayank",
        resource_id="/subscriptions/sub-1/resourceGroups/rg-mayank/providers/Microsoft.CognitiveServices/accounts/mayank-proxy",
        endpoint="https://mayank.openai.azure.com/",
        openai_api_key_encrypted="stored",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class RevealApiKey(unittest.IsolatedAsyncioTestCase):
    def _service(self, account):
        repo = SimpleNamespace(
            _session=object(),
            get=AsyncMock(return_value=account),
        )
        box = SimpleNamespace(decrypt=lambda _value: "sp-secret")
        return AccountService(repo, box)

    async def test_returns_stored_key(self):
        account = _account()
        service = self._service(account)
        with patch("app.services.openai_key_store.decrypt_foundry_key", new=AsyncMock(return_value=" foundry-key ")) as decrypt:
            result = await service.reveal_api_key(account.id)
        decrypt.assert_awaited_once()
        self.assertEqual(result["api_key"], "foundry-key")
        self.assertEqual(result["endpoint"], account.endpoint)

    async def test_missing_account(self):
        service = self._service(None)
        with self.assertRaises(AccountNotFoundError):
            await service.reveal_api_key(99)

    async def test_fetches_from_azure_when_not_stored(self):
        account = _account(openai_api_key_encrypted=None)
        service = self._service(account)
        arm = SimpleNamespace(post=AsyncMock(return_value={"key1": "azure-key"}))
        with (
            patch("app.services.openai_key_store.decrypt_foundry_key", new=AsyncMock(return_value=None)),
            patch("app.providers.azure.arm_client.AzureArmClient", return_value=arm),
            patch("app.providers.azure.token_provider.AzureTokenProvider", return_value=object()),
            patch("app.services.openai_key_store.persist_foundry_api_keys", new=AsyncMock()) as persist,
        ):
            result = await service.reveal_api_key(account.id)
        self.assertEqual(result["api_key"], "azure-key")
        persist.assert_awaited_once()

    async def test_errors_when_no_key_anywhere(self):
        account = _account(openai_api_key_encrypted=None)
        service = self._service(account)
        arm = SimpleNamespace(post=AsyncMock(side_effect=AzureApiError("Azure API error (403): Forbidden")))
        with (
            patch("app.services.openai_key_store.decrypt_foundry_key", new=AsyncMock(return_value=None)),
            patch("app.providers.azure.arm_client.AzureArmClient", return_value=arm),
            patch("app.providers.azure.token_provider.AzureTokenProvider", return_value=object()),
        ):
            with self.assertRaises(AccountValidationError) as ctx:
                await service.reveal_api_key(account.id)
        self.assertIn("403", str(ctx.exception))


class RotateApiKey(unittest.IsolatedAsyncioTestCase):
    def _service(self, account):
        repo = SimpleNamespace(
            _session=object(),
            get=AsyncMock(return_value=account),
        )
        box = SimpleNamespace(decrypt=lambda _value: "sp-secret")
        return AccountService(repo, box)

    async def test_rotates_key1_and_updates_newapi(self):
        account = _account()
        service = self._service(account)
        arm = SimpleNamespace(post=AsyncMock(return_value={"key1": "new-key"}))
        with (
            patch("app.providers.azure.arm_client.AzureArmClient", return_value=arm),
            patch("app.providers.azure.token_provider.AzureTokenProvider", return_value=object()),
            patch.object(service, "_store_foundry_key", new=AsyncMock()) as store,
            patch.object(service, "_push_newapi_key", new=AsyncMock(return_value="")) as push,
        ):
            result = await service.rotate_api_key(account.id)
        arm.post.assert_awaited_once()
        self.assertIn("/regenerateKey", arm.post.await_args.args[1])
        self.assertEqual(arm.post.await_args.kwargs["json"], {"keyName": "Key1"})
        store.assert_awaited_once()
        push.assert_awaited_once_with(account, "new-key")
        self.assertEqual(result["api_key"], "new-key")
        self.assertIsNone(result["new_api_error"])

    async def test_returns_newapi_warning_after_successful_rotate(self):
        account = _account()
        service = self._service(account)
        arm = SimpleNamespace(post=AsyncMock(return_value={"key1": "new-key"}))
        with (
            patch("app.providers.azure.arm_client.AzureArmClient", return_value=arm),
            patch("app.providers.azure.token_provider.AzureTokenProvider", return_value=object()),
            patch.object(service, "_store_foundry_key", new=AsyncMock()),
            patch.object(service, "_push_newapi_key", new=AsyncMock(return_value="O1 token expired")),
        ):
            result = await service.rotate_api_key(account.id)
        self.assertEqual(result["api_key"], "new-key")
        self.assertEqual(result["new_api_error"], "O1 token expired")

    async def test_rotate_azure_error(self):
        account = _account()
        service = self._service(account)
        arm = SimpleNamespace(post=AsyncMock(side_effect=AzureApiError("Azure API error (403): Forbidden")))
        with (
            patch("app.providers.azure.arm_client.AzureArmClient", return_value=arm),
            patch("app.providers.azure.token_provider.AzureTokenProvider", return_value=object()),
        ):
            with self.assertRaises(AccountValidationError) as ctx:
                await service.rotate_api_key(account.id)
        self.assertIn("403", str(ctx.exception))
