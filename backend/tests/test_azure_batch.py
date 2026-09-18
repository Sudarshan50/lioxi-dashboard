import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.core.exceptions import AzureApiError
from app.providers.azure.arm_client import AzureArmClient
from app.providers.base import ProviderCredentials
from app.runtime import azure_batch_size, gather_batched


class GatherBatched(unittest.IsolatedAsyncioTestCase):
    async def test_empty(self):
        self.assertEqual(await gather_batched([]), [])

    async def test_preserves_order_and_caps_in_flight(self):
        running = 0
        peak = 0

        async def one(index: int) -> int:
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.01)
            running -= 1
            return index

        out = await gather_batched([one(index) for index in range(5)], batch_size=2)
        self.assertEqual(out, [0, 1, 2, 3, 4])
        self.assertLessEqual(peak, 2)

    def test_batch_size_caps_at_count(self):
        self.assertEqual(azure_batch_size(3, jobs=8), 3)
        self.assertEqual(azure_batch_size(20, jobs=8), 8)
        self.assertEqual(azure_batch_size(0, jobs=8), 0)
        self.assertEqual(azure_batch_size(5, jobs=0), 1)


class ArmRetry(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import app.providers.azure.arm_client as arm_mod

        arm_mod._arm_limit = None

    async def test_retries_429_then_succeeds(self):
        creds = ProviderCredentials("t", "c", "s", "sub")
        token = AsyncMock()
        token.get_token = AsyncMock(return_value="tok")
        responses = [
            _FakeResponse(429, {"error": {"message": "Too many requests. Please retry."}}),
            _FakeResponse(200, {"value": [{"id": "ok"}]}),
        ]
        client = _FakeHttpx(responses)
        arm = AzureArmClient(token)
        with (
            patch("app.providers.azure.arm_client.httpx.AsyncClient", return_value=client),
            patch("app.providers.azure.arm_client.asyncio.sleep", new_callable=AsyncMock),
        ):
            body = await arm.get(creds, "/subscriptions/sub")
        self.assertEqual(body["value"][0]["id"], "ok")
        self.assertEqual(client.calls, 2)

    async def test_non_throttle_does_not_retry(self):
        creds = ProviderCredentials("t", "c", "s", "sub")
        token = AsyncMock()
        token.get_token = AsyncMock(return_value="tok")
        client = _FakeHttpx([_FakeResponse(404, {"error": {"message": "missing"}})])
        arm = AzureArmClient(token)
        with patch("app.providers.azure.arm_client.httpx.AsyncClient", return_value=client):
            with self.assertRaises(AzureApiError) as caught:
                await arm.get(creds, "/subscriptions/sub")
        self.assertIn("(404)", str(caught.exception))
        self.assertEqual(client.calls, 1)

    async def test_gives_up_after_retries(self):
        creds = ProviderCredentials("t", "c", "s", "sub")
        token = AsyncMock()
        token.get_token = AsyncMock(return_value="tok")
        client = _FakeHttpx(
            [_FakeResponse(429, {"error": {"message": "Too many requests. Please retry."}})] * 4
        )
        arm = AzureArmClient(token)
        with (
            patch("app.providers.azure.arm_client.httpx.AsyncClient", return_value=client),
            patch("app.providers.azure.arm_client.asyncio.sleep", new_callable=AsyncMock),
        ):
            with self.assertRaises(AzureApiError) as caught:
                await arm.get(creds, "/subscriptions/sub")
        self.assertIn("(429)", str(caught.exception))
        self.assertEqual(client.calls, 4)


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload


class _FakeHttpx:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def request(self, method, url, **kwargs):
        self.calls += 1
        if not self._responses:
            raise AssertionError("no more fake ARM responses")
        return self._responses.pop(0)


if __name__ == "__main__":
    unittest.main()
