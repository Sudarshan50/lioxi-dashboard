import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.providers.azure.metrics import AzureMetricsService, _to_token_usage
from app.providers.base import ProviderCredentials, TokenUsage
from app.services.azure_token_totals import apply_cached_account_tokens, cache_end, totals_by_range
from app.services.sync_service import SyncService


def _point(hour: int, prompt: int, completion: int) -> TokenUsage:
    return TokenUsage(
        bucket_start=datetime(2026, 9, 14, hour, tzinfo=timezone.utc),
        prompt_tokens=prompt,
        cached_tokens=0,
        completion_tokens=completion,
        total_tokens=prompt + completion,
        request_count=1,
    )


class TotalsByRange(unittest.TestCase):
    def test_sums_input_and_output_not_total_tokens(self):
        end = datetime(2026, 9, 14, 12, tzinfo=timezone.utc)
        points = [
            TokenUsage(
                bucket_start=end - timedelta(hours=2),
                prompt_tokens=100,
                cached_tokens=40,
                completion_tokens=7,
                total_tokens=999,
                request_count=1,
            ),
            TokenUsage(
                bucket_start=end - timedelta(hours=30),
                prompt_tokens=50,
                cached_tokens=0,
                completion_tokens=3,
                total_tokens=999,
                request_count=1,
            ),
        ]
        totals = totals_by_range(points, end)
        self.assertEqual(totals["24h"], {"input": 100, "output": 7})
        self.assertEqual(totals["7d"]["input"], 150)
        self.assertEqual(totals["7d"]["output"], 10)

    def test_excludes_current_hour_and_older_than_range(self):
        end = datetime(2026, 9, 14, 12, tzinfo=timezone.utc)
        points = [
            _point(12, 9, 9),
            _point(11, 4, 1),
            TokenUsage(
                bucket_start=end - timedelta(days=8),
                prompt_tokens=80,
                cached_tokens=0,
                completion_tokens=8,
                total_tokens=88,
                request_count=1,
            ),
        ]
        totals = totals_by_range(points, end)
        self.assertEqual(totals["24h"], {"input": 4, "output": 1})
        self.assertEqual(totals["7d"], {"input": 4, "output": 1})
        self.assertEqual(totals["30d"], {"input": 84, "output": 9})


class ApplyCachedTokens(unittest.IsolatedAsyncioTestCase):
    async def test_replaces_overview_with_cached_sums(self):
        overview = {"total_prompt_tokens": 1, "total_completion_tokens": 2}
        accounts = [
            SimpleNamespace(id=1, azure_token_totals={"7d": {"input": 100, "output": 20}}),
            SimpleNamespace(id=2, azure_token_totals={"7d": {"input": 50, "output": 5}}),
        ]
        await apply_cached_account_tokens(overview, accounts, "7d", None, AsyncMock())
        self.assertEqual(overview["total_prompt_tokens"], 150)
        self.assertEqual(overview["total_completion_tokens"], 25)

    async def test_keeps_snapshots_when_nothing_is_cached(self):
        overview = {"total_prompt_tokens": 1, "total_completion_tokens": 2}
        snapshot = AsyncMock()
        await apply_cached_account_tokens(
            overview, [SimpleNamespace(id=1, azure_token_totals=None)], "7d", None, snapshot
        )
        self.assertEqual(overview["total_prompt_tokens"], 1)
        snapshot.assert_not_awaited()

    async def test_adds_snapshots_only_for_uncached_accounts(self):
        overview = {"total_prompt_tokens": 1, "total_completion_tokens": 2}

        async def snapshot_totals(ids):
            self.assertEqual(ids, [9])
            return {"total_prompt_tokens": 15, "total_completion_tokens": 4}

        accounts = [
            SimpleNamespace(id=1, azure_token_totals={"7d": {"input": 40, "output": 6}}),
            SimpleNamespace(id=9, azure_token_totals=None),
        ]
        await apply_cached_account_tokens(overview, accounts, "7d", None, snapshot_totals)
        self.assertEqual(overview["total_prompt_tokens"], 55)
        self.assertEqual(overview["total_completion_tokens"], 10)

    async def test_model_filter_keeps_snapshots(self):
        overview = {"total_prompt_tokens": 1, "total_completion_tokens": 2}
        accounts = [SimpleNamespace(id=1, azure_token_totals={"7d": {"input": 40, "output": 6}})]
        await apply_cached_account_tokens(overview, accounts, "7d", 9, AsyncMock())
        self.assertEqual(overview["total_prompt_tokens"], 1)


class AccountMetricsRequest(unittest.IsolatedAsyncioTestCase):
    async def test_account_fetch_has_no_deployment_filter(self):
        arm = SimpleNamespace(get=AsyncMock(return_value={"value": []}))
        service = AzureMetricsService(arm)
        creds = ProviderCredentials("t", "c", "s", "sub")
        await service.get_account_input_output(
            creds,
            "/subscriptions/x/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/a",
            "AIServices",
            datetime(2026, 9, 1, tzinfo=timezone.utc),
            datetime(2026, 9, 14, tzinfo=timezone.utc),
        )
        params = arm.get.await_args.kwargs["params"]
        self.assertNotIn("$filter", params)
        self.assertEqual(params["metricnames"], "InputTokens,OutputTokens")

    def test_foundry_parser_reads_input_and_output_metrics(self):
        body = {
            "value": [
                {
                    "name": {"value": "InputTokens"},
                    "timeseries": [{"data": [{"timeStamp": "2026-09-14T10:00:00Z", "total": 11}]}],
                },
                {
                    "name": {"value": "OutputTokens"},
                    "timeseries": [{"data": [{"timeStamp": "2026-09-14T10:00:00Z", "total": 3}]}],
                },
            ]
        }
        usage = _to_token_usage(body, "AIServices")
        self.assertEqual(len(usage), 1)
        self.assertEqual(usage[0].prompt_tokens, 11)
        self.assertEqual(usage[0].completion_tokens, 3)


class CacheAccountIo(unittest.IsolatedAsyncioTestCase):
    async def test_failed_fetch_leaves_existing_cache(self):
        account = SimpleNamespace(
            name="gone",
            resource_id="/r",
            kind="AIServices",
            azure_token_totals={"7d": {"input": 9, "output": 1}},
        )

        async def boom(*_args, **_kwargs):
            raise RuntimeError("404")

        provider = SimpleNamespace(get_account_input_output=boom)
        await SyncService(AsyncMock(), AsyncMock())._cache_account_input_output(
            provider, SimpleNamespace(), account
        )
        self.assertEqual(account.azure_token_totals["7d"]["input"], 9)

    def test_floors_to_the_hour(self):
        now = datetime(2026, 9, 14, 13, 44, 12, tzinfo=timezone.utc)
        self.assertEqual(cache_end(now), datetime(2026, 9, 14, 13, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
