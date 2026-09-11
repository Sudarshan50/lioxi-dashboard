"""On-demand Azure credit grants for pending /join submissions."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.sp_submit_request import SpSubmitRequest
from app.runtime import AZURE_SYNC_CONCURRENCY
from app.schemas.kimi_deploy import KimiCreditSnapshot
from app.schemas.submit import PendingGrantAccount, PendingGrantSummary, PendingGrantsResponse, PendingRequestPublic
from app.services.join_group import normalize_group

logger = logging.getLogger(__name__)

ONE_K_GRANT_MIN = 800
ONE_K_GRANT_MAX = 1500
TEN_K_GRANT_MIN = 8000
TEN_K_GRANT_MAX = 15000
STATUS_PENDING = "pending_approval"

_refresh_lock = asyncio.Lock()


@dataclass(frozen=True)
class _GrantJob:
    id: int
    name: str
    subscription_id: str
    snapshot: KimiCreditSnapshot | None = None
    payload: dict[str, str] | None = None


def grant_amount_usd(limit: float | None, currency: str | None) -> float | None:
    if limit is None or float(limit) <= 0:
        return None
    code = (currency or "USD").upper()
    value = float(limit)
    if code == "INR":
        rate = get_settings().usd_inr_rate or 87
        if rate <= 0:
            return None
        return value / rate
    return value


def classify_grant_tier(grant_usd: float | None) -> str | None:
    if grant_usd is None:
        return None
    if TEN_K_GRANT_MIN <= grant_usd <= TEN_K_GRANT_MAX:
        return "10k"
    if ONE_K_GRANT_MIN <= grant_usd <= ONE_K_GRANT_MAX:
        return "1k"
    return "other"


def public_grant_fields(row: Any) -> dict[str, Any]:
    available = bool(getattr(row, "credits_available", False))
    grant = (
        grant_amount_usd(getattr(row, "credits_limit", None), getattr(row, "credits_currency", None))
        if available
        else None
    )
    fetched_at = getattr(row, "credits_fetched_at", None)
    return {
        "credits_limit": getattr(row, "credits_limit", None) if available else None,
        "credits_remaining": getattr(row, "credits_remaining", None) if available else None,
        "credits_used": getattr(row, "credits_used", None) if available else None,
        "credits_currency": getattr(row, "credits_currency", None) if available else None,
        "credits_label": getattr(row, "credits_label", None) if available else None,
        "credits_available": available,
        "credits_fetched_at": fetched_at,
        "credits_error": getattr(row, "credits_error", None),
        "grant_usd": grant,
        "grant_tier": classify_grant_tier(grant),
    }


def grant_account_from_row(row: Any) -> PendingGrantAccount:
    fields = public_grant_fields(row)
    return PendingGrantAccount(
        id=row.id,
        email=row.account_holder,
        name=row.name,
        person_associated=row.person_associated,
        group_tag=normalize_group(getattr(row, "group_tag", None)),
        subscription_id=row.subscription_id,
        credits_limit=fields["credits_limit"],
        credits_remaining=fields["credits_remaining"],
        credits_currency=fields["credits_currency"],
        credits_available=fields["credits_available"],
        credits_fetched_at=fields["credits_fetched_at"],
        credits_error=fields["credits_error"],
        grant_usd=fields["grant_usd"],
        grant_tier=fields["grant_tier"],
    )


def summarize_grant_accounts(accounts: list[PendingGrantAccount]) -> PendingGrantSummary:
    pool = 0.0
    pool_10k = 0.0
    pool_1k = 0.0
    count_10k = 0
    count_1k = 0
    count_other = 0
    fetched = 0
    failed = 0
    missing = 0
    latest: datetime | None = None
    for item in accounts:
        if item.credits_fetched_at is None:
            missing += 1
        else:
            fetched += 1
            if latest is None or item.credits_fetched_at > latest:
                latest = item.credits_fetched_at
            if not item.credits_available:
                failed += 1
        if not item.credits_available:
            continue
        grant = item.grant_usd or 0.0
        if item.grant_tier == "10k":
            count_10k += 1
            pool_10k += grant
            pool += grant
        elif item.grant_tier == "1k":
            count_1k += 1
            pool_1k += grant
            pool += grant
        elif item.grant_tier == "other":
            count_other += 1
            pool += grant
    return PendingGrantSummary(
        total=len(accounts),
        fetched=fetched,
        missing=missing,
        failed=failed,
        pool_usd=round(pool, 2),
        count_10k=count_10k,
        count_1k=count_1k,
        count_other=count_other,
        pool_10k_usd=round(pool_10k, 2),
        pool_1k_usd=round(pool_1k, 2),
        fetched_at=latest,
    )


def summarize_pending_public(rows: list[PendingRequestPublic]) -> PendingGrantSummary:
    waiting = [row for row in rows if row.status == "pending_approval"]
    accounts = [
        PendingGrantAccount(
            id=row.id,
            email=row.account_holder,
            name=row.name,
            person_associated=row.person_associated,
            group_tag=normalize_group(getattr(row, "group_tag", None)),
            subscription_id=row.subscription_id,
            credits_limit=row.credits_limit,
            credits_remaining=row.credits_remaining,
            credits_currency=row.credits_currency,
            credits_available=row.credits_available,
            credits_fetched_at=row.credits_fetched_at,
            credits_error=row.credits_error,
            grant_usd=row.grant_usd,
            grant_tier=row.grant_tier,
        )
        for row in waiting
    ]
    return summarize_grant_accounts(accounts)


def apply_credit_snapshot(row: Any, snapshot: KimiCreditSnapshot, fetched_at: datetime) -> None:
    row.credits_fetched_at = fetched_at
    limit = snapshot.credits_limit
    if snapshot.ok and snapshot.credits_available and limit is not None and float(limit) > 0:
        row.credits_limit = snapshot.credits_limit
        row.credits_remaining = snapshot.credits_remaining
        row.credits_used = snapshot.credits_used
        row.credits_currency = snapshot.credits_currency or "USD"
        row.credits_label = snapshot.credits_label
        row.credits_available = True
        row.credits_error = None
        return
    row.credits_available = False
    row.credits_error = (snapshot.error or "Azure did not return a credit grant for this subscription.")[:800]
    row.credits_limit = None
    row.credits_remaining = None
    row.credits_used = None
    row.credits_currency = None
    row.credits_label = None


def _failed_snapshot(name: str, subscription_id: str, error: str) -> KimiCreditSnapshot:
    return KimiCreditSnapshot(ok=False, name=name, subscription_id=subscription_id, error=error)


def _job_from_row(row: Any) -> _GrantJob:
    from app.core.crypto import get_secret_box
    from app.services.az_cli_session import is_tenant_level_account
    from app.services.submit_service import deploy_payload_from_row

    name = row.name or row.person_associated or row.account_holder or "account"
    subscription_id = row.subscription_id or ""
    if is_tenant_level_account(
        subscription_id=str(row.subscription_id or ""),
        tenant_id=str(row.tenant_id or ""),
        name=str(row.subscription_name or ""),
    ):
        return _GrantJob(
            id=row.id,
            name=name,
            subscription_id=subscription_id,
            snapshot=_failed_snapshot(name, subscription_id, "This is a Microsoft tenant login, not an Azure subscription."),
        )
    if not row.client_secret_encrypted or not row.client_id or not row.tenant_id or not row.subscription_id:
        return _GrantJob(
            id=row.id,
            name=name,
            subscription_id=subscription_id,
            snapshot=_failed_snapshot(
                name,
                subscription_id,
                "No stored identity yet. Wait until this submission is ready to approve.",
            ),
        )
    try:
        secret = get_secret_box().decrypt(row.client_secret_encrypted)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Pending grant decrypt failed for %s: %s", row.id, str(exc)[-200:])
        return _GrantJob(
            id=row.id,
            name=name,
            subscription_id=subscription_id,
            snapshot=_failed_snapshot(name, subscription_id, "Could not decrypt the stored identity."),
        )
    return _GrantJob(
        id=row.id,
        name=name,
        subscription_id=subscription_id,
        payload=deploy_payload_from_row(row, secret),
    )


async def _run_grant_job(job: _GrantJob) -> KimiCreditSnapshot:
    if job.snapshot is not None:
        return job.snapshot
    from app.services.kimi_deploy_service import lookup_account_credits

    try:
        return await lookup_account_credits(job.payload or {})
    except Exception as exc:  # noqa: BLE001
        logger.warning("Pending grant lookup failed for %s: %s", job.id, str(exc)[-300:])
        return _failed_snapshot(job.name, job.subscription_id, str(exc)[-400:])


async def stored_pending_grants(db: AsyncSession) -> PendingGrantsResponse:
    from app.services.submit_service import list_pending

    rows = [row for row in await list_pending(db) if row.status == STATUS_PENDING]
    accounts = [grant_account_from_row(row) for row in rows]
    return PendingGrantsResponse(ok=True, summary=summarize_grant_accounts(accounts), accounts=accounts)


async def refresh_pending_grants(db: AsyncSession) -> PendingGrantsResponse:
    from app.services.submit_service import list_pending

    async with _refresh_lock:
        rows = [row for row in await list_pending(db) if row.status == STATUS_PENDING]
        if not rows:
            return PendingGrantsResponse(ok=True, summary=PendingGrantSummary(), accounts=[])
        jobs = [_job_from_row(row) for row in rows]
        sem = asyncio.Semaphore(max(1, AZURE_SYNC_CONCURRENCY))

        async def one(job: _GrantJob) -> KimiCreditSnapshot:
            async with sem:
                return await _run_grant_job(job)

        snapshots = await asyncio.gather(*[one(job) for job in jobs])
        live = (
            await db.execute(
                select(SpSubmitRequest).where(
                    SpSubmitRequest.id.in_([job.id for job in jobs]),
                    SpSubmitRequest.status == STATUS_PENDING,
                )
            )
        ).scalars()
        by_id = {row.id: row for row in live}
        fetched_at = datetime.now(timezone.utc)
        accounts: list[PendingGrantAccount] = []
        for job, snapshot in zip(jobs, snapshots):
            row = by_id.get(job.id)
            if row is None:
                continue
            apply_credit_snapshot(row, snapshot, fetched_at)
            accounts.append(grant_account_from_row(row))
        await db.commit()
        return PendingGrantsResponse(ok=True, summary=summarize_grant_accounts(accounts), accounts=accounts)
