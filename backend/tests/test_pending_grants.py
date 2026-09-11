import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.schemas.kimi_deploy import KimiCreditSnapshot
from app.schemas.submit import PendingGrantAccount, PendingRequestPublic
from app.services.pending_grants import (
    _GrantJob,
    apply_credit_snapshot,
    classify_grant_tier,
    grant_account_from_row,
    grant_amount_usd,
    public_grant_fields,
    summarize_grant_accounts,
    summarize_pending_public,
)


def _row(**kwargs):
    defaults = {
        "id": 1,
        "account_holder": "user@example.com",
        "name": "acct",
        "person_associated": "sam",
        "subscription_id": "sub-1",
        "credits_limit": None,
        "credits_remaining": None,
        "credits_used": None,
        "credits_currency": None,
        "credits_label": None,
        "credits_available": False,
        "credits_fetched_at": None,
        "credits_error": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class GrantMath(unittest.TestCase):
    def test_usd_passthrough(self):
        self.assertEqual(grant_amount_usd(10000, "USD"), 10000)
        self.assertIsNone(grant_amount_usd(0, "USD"))
        self.assertIsNone(grant_amount_usd(None, "USD"))

    def test_inr_converts(self):
        with patch("app.services.pending_grants.get_settings", return_value=SimpleNamespace(usd_inr_rate=87)):
            self.assertAlmostEqual(grant_amount_usd(87000, "INR") or 0, 1000)

    def test_tiers(self):
        self.assertEqual(classify_grant_tier(10000), "10k")
        self.assertEqual(classify_grant_tier(10200), "10k")
        self.assertEqual(classify_grant_tier(1000), "1k")
        self.assertEqual(classify_grant_tier(1200), "1k")
        self.assertEqual(classify_grant_tier(5000), "other")
        self.assertIsNone(classify_grant_tier(None))


class GrantSummary(unittest.TestCase):
    def test_counts_10k_and_1k_pool(self):
        now = datetime.now(timezone.utc)
        accounts = [
            PendingGrantAccount(
                id=1,
                email="ten@example.com",
                credits_available=True,
                credits_fetched_at=now,
                grant_usd=10000,
                grant_tier="10k",
            ),
            PendingGrantAccount(
                id=2,
                email="one@example.com",
                credits_available=True,
                credits_fetched_at=now,
                grant_usd=1000,
                grant_tier="1k",
            ),
            PendingGrantAccount(id=3, email="new@example.com"),
            PendingGrantAccount(
                id=4,
                email="fail@example.com",
                credits_fetched_at=now,
                credits_error="Azure did not return a credit grant for this subscription.",
                credits_available=False,
                grant_usd=None,
                grant_tier=None,
            ),
        ]
        summary = summarize_grant_accounts(accounts)
        self.assertEqual(summary.total, 4)
        self.assertEqual(summary.fetched, 3)
        self.assertEqual(summary.missing, 1)
        self.assertEqual(summary.failed, 1)
        self.assertEqual(summary.count_10k, 1)
        self.assertEqual(summary.count_1k, 1)
        self.assertEqual(summary.pool_usd, 11000)
        self.assertEqual(summary.pool_10k_usd, 10000)
        self.assertEqual(summary.pool_1k_usd, 1000)

    def test_pending_list_only_counts_waiting(self):
        now = datetime.now(timezone.utc)
        rows = [
            PendingRequestPublic(
                id=1,
                status="pending_approval",
                account_holder="ten@example.com",
                credits_available=True,
                credits_fetched_at=now,
                grant_usd=10000,
                grant_tier="10k",
            ),
            PendingRequestPublic(
                id=2,
                status="failed",
                account_holder="skip@example.com",
                credits_available=True,
                credits_fetched_at=now,
                grant_usd=10000,
                grant_tier="10k",
            ),
        ]
        summary = summarize_pending_public(rows)
        self.assertEqual(summary.total, 1)
        self.assertEqual(summary.count_10k, 1)
        self.assertEqual(summary.pool_usd, 10000)


class ApplySnapshot(unittest.TestCase):
    def test_stores_successful_grant(self):
        row = _row()
        now = datetime.now(timezone.utc)
        apply_credit_snapshot(
            row,
            KimiCreditSnapshot(
                ok=True,
                name="acct",
                credits_limit=10200,
                credits_remaining=9800,
                credits_used=400,
                credits_currency="USD",
                credits_label="Azure credits",
                credits_available=True,
            ),
            now,
        )
        self.assertEqual(row.credits_limit, 10200)
        self.assertTrue(row.credits_available)
        self.assertIsNone(row.credits_error)
        self.assertEqual(row.credits_fetched_at, now)
        fields = public_grant_fields(row)
        self.assertEqual(fields["grant_usd"], 10200)
        self.assertEqual(fields["grant_tier"], "10k")
        self.assertEqual(grant_account_from_row(row).email, "user@example.com")

    def test_zero_limit_is_failure(self):
        now = datetime.now(timezone.utc)
        row = _row()
        apply_credit_snapshot(
            row,
            KimiCreditSnapshot(ok=True, name="acct", credits_limit=0, credits_available=True),
            now,
        )
        self.assertFalse(row.credits_available)
        self.assertIsNone(row.credits_limit)
        self.assertIsNone(public_grant_fields(row)["grant_usd"])

    def test_clears_grant_on_failure(self):
        now = datetime.now(timezone.utc)
        row = _row(credits_limit=10000, credits_currency="USD", credits_available=True)
        apply_credit_snapshot(
            row,
            KimiCreditSnapshot(ok=False, name="acct", error="timeout"),
            now,
        )
        self.assertIsNone(row.credits_limit)
        self.assertFalse(row.credits_available)
        self.assertEqual(row.credits_error, "timeout")
        self.assertEqual(row.credits_fetched_at, now)
        fields = public_grant_fields(row)
        self.assertIsNone(fields["grant_usd"])
        self.assertIsNone(fields["grant_tier"])


class RefreshGrants(unittest.IsolatedAsyncioTestCase):
    async def test_refresh_writes_lookups(self):
        now_row = _row(id=7, account_holder="live@example.com", status="pending_approval")
        snapshot = KimiCreditSnapshot(
            ok=True,
            name="acct",
            credits_limit=1000,
            credits_currency="USD",
            credits_available=True,
        )
        job = _GrantJob(id=7, name="acct", subscription_id="sub-1", payload={"name": "acct"})
        execute_result = SimpleNamespace(scalars=lambda: [now_row])
        db = AsyncMock()
        db.execute = AsyncMock(return_value=execute_result)

        with (
            patch("app.services.submit_service.list_pending", new=AsyncMock(return_value=[now_row])),
            patch("app.services.pending_grants._job_from_row", return_value=job),
            patch("app.services.pending_grants._run_grant_job", new=AsyncMock(return_value=snapshot)),
        ):
            from app.services.pending_grants import refresh_pending_grants

            result = await refresh_pending_grants(db)
        self.assertEqual(result.summary.count_1k, 1)
        self.assertEqual(result.summary.pool_usd, 1000)
        self.assertEqual(result.accounts[0].email, "live@example.com")
        db.commit.assert_awaited()

    async def test_skips_rows_that_left_pending(self):
        now_row = _row(id=7, account_holder="gone@example.com", status="pending_approval")
        snapshot = KimiCreditSnapshot(
            ok=True,
            name="acct",
            credits_limit=10000,
            credits_currency="USD",
            credits_available=True,
        )
        job = _GrantJob(id=7, name="acct", subscription_id="sub-1", payload={"name": "acct"})
        execute_result = SimpleNamespace(scalars=lambda: [])
        db = AsyncMock()
        db.execute = AsyncMock(return_value=execute_result)

        with (
            patch("app.services.submit_service.list_pending", new=AsyncMock(return_value=[now_row])),
            patch("app.services.pending_grants._job_from_row", return_value=job),
            patch("app.services.pending_grants._run_grant_job", new=AsyncMock(return_value=snapshot)),
        ):
            from app.services.pending_grants import refresh_pending_grants

            result = await refresh_pending_grants(db)
        self.assertEqual(result.summary.total, 0)
        self.assertIsNone(now_row.credits_limit)
        db.commit.assert_awaited()


if __name__ == "__main__":
    unittest.main()
