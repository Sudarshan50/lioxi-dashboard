from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sync_notification import SyncNotification

KEEP = 500
KIND_TPM_UPGRADE = "tpm_upgrade"


async def add_log(
    session: AsyncSession,
    *,
    kind: str,
    status: str,
    detail: str,
    email: str | None = None,
    owner_tag: str | None = None,
    account_name: str | None = None,
    resource_name: str | None = None,
    subscription_id: str | None = None,
    new_api_name: str | None = None,
    error: str | None = None,
) -> None:
    session.add(
        SyncNotification(
            kind=kind,
            status=status,
            email=(email or "").strip() or None,
            owner_tag=(owner_tag or "").strip() or None,
            account_name=(account_name or "").strip() or None,
            resource_name=(resource_name or "").strip() or None,
            subscription_id=(subscription_id or "").strip() or None,
            new_api_name=(new_api_name or "").strip() or None,
            detail=detail[:256],
            error=(error or "")[:500] or None,
        )
    )


async def prune(session: AsyncSession, keep: int = KEEP) -> None:
    cutoff = (
        await session.execute(
            select(SyncNotification.id).order_by(SyncNotification.id.desc()).offset(keep).limit(1)
        )
    ).scalar_one_or_none()
    if cutoff is None:
        return
    await session.execute(delete(SyncNotification).where(SyncNotification.id <= cutoff))


async def list_logs(session: AsyncSession, limit: int = 100) -> tuple[list[SyncNotification], int]:
    total = int((await session.execute(select(func.count()).select_from(SyncNotification))).scalar_one())
    rows = (
        await session.execute(
            select(SyncNotification).order_by(SyncNotification.id.desc()).limit(max(1, min(limit, 200)))
        )
    ).scalars()
    return list(rows), total
