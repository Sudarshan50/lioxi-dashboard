"""Join-wizard submit flow: device-code login, SP + roles, pending approval."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import shutil
import string
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.crypto import get_secret_box
from app.models.azure_service_principal import AzureServicePrincipal
from app.models.provider_account import ProviderAccount
from app.models.sp_submit_request import SpSubmitRequest
from app.schemas.submit import PendingRequestPublic, SubmitSessionSnapshot, SubmitSubscription
from app.services.account_service import allocate_unique_name
from app.services.pending_grants import public_grant_fields
from app.services.az_cli_session import (
    AzCliError,
    drop_az_session,
    get_az_session,
    is_tenant_level_account,
    normalize_tenant_id,
    scrub_az_text,
)
from app.services.deploy_defaults import resolve_routing
from app.services.join_enrollee_service import (
    EnrolleeError,
    ensure_enrollee_for_submit,
    list_join_picker_names,
    validate_enrollee_for_submit,
)
from app.services.join_group import join_account_name, normalize_group
from app.services.kimi_deploy_service import load_deploy_module
from app.services.service_principal_store import persist_service_principals

logger = logging.getLogger(__name__)

ProgressFn = Callable[[dict[str, Any]], Awaitable[None]]

STATUS_LOGIN_STARTED = "login_started"
STATUS_LOGGED_IN = "logged_in"
STATUS_CREATING_SP = "creating_sp"
STATUS_PENDING = "pending_approval"
STATUS_APPROVING = "approving"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_FAILED = "failed"
STATUS_EXPIRED = "expired"
AUTH_ADMIN = "admin"
AUTH_AUTO = "auto-approve"

OPEN_LOGIN = {STATUS_LOGIN_STARTED, STATUS_LOGGED_IN}
TERMINAL = {STATUS_APPROVED, STATUS_REJECTED, STATUS_FAILED, STATUS_EXPIRED}
LIVE_SUB_STATUSES = (STATUS_PENDING, STATUS_CREATING_SP, STATUS_APPROVING)
DUPLICATE_SUB_MESSAGE = (
    "This Azure subscription is already submitted. An admin must decline it before you can register again."
)
ALREADY_ONBOARDED_MESSAGE = "This Azure subscription is already in the portal."

SP_NAME = "usage-and-credits-monitor"
# Arbitrary constant; only has to be stable and unique among our advisory locks.
NAME_ALLOCATION_LOCK = 8_724_193_055_110_001
_NAME_CONSTRAINT = "uq_sp_submit_name"
_ENROLLEE_CONSTRAINT = "uq_join_enrollee_name_group"


def _constraint_in_error(exc: IntegrityError, name: str) -> bool:
    return name in str(getattr(exc, "orig", exc))


def _is_duplicate_name_error(exc: IntegrityError) -> bool:
    return _constraint_in_error(exc, _NAME_CONSTRAINT)


def _is_duplicate_enrollee_error(exc: IntegrityError) -> bool:
    return _constraint_in_error(exc, _ENROLLEE_CONSTRAINT)


def _grant_steps() -> tuple[list[str], set[str], int]:
    """Roles to assign, which of them are mandatory, and the progress step count.

    A step is the monitor identity, then each role, then billing reader. The
    Join wizard sizes its progress bar from this before any Azure call runs,
    so the count has to be known up front rather than discovered on the way.
    """
    mod = load_deploy_module()
    roles: list[str] = list(getattr(mod, "ALL_ROLES", []))
    if "Contributor" in roles:
        roles = ["Contributor"] + [item for item in roles if item != "Contributor"]
    admin_roles = set(getattr(mod, "ADMIN_ROLES", _DEFAULT_ADMIN_ROLES))
    return roles, admin_roles, len(roles) + 2


CREATING_SP_MAX_AGE = timedelta(minutes=25)
APPROVING_MAX_AGE = timedelta(minutes=45)
LOGIN_TASK_GRACE = timedelta(seconds=45)
OPEN_LOGIN_INTERRUPTED = "Sign-in was interrupted. Start Azure sign-in again."
_DEFAULT_ADMIN_ROLES = [
    "Contributor",
    "Cognitive Services Contributor",
    "Foundry Owner",
    "Foundry User",
    "Azure AI Developer",
]
_TOKEN_SCOPE = "https://management.azure.com/.default"
ERROR_KIND_ACCOUNT = "account"
ERROR_KIND_NETWORK = "network"
ERROR_KIND_ROLES = "roles"
ERROR_KIND_DEPLOY = "deploy"
_INVALID_SECRET_MARKERS = (
    "aadsts7000215",
    "aadsts7000222",
    "aadsts7000218",
    "invalid_client",
    "invalid client secret",
)

_NETWORK_MARKERS = (
    "etimedout",
    "econnrefused",
    "econnreset",
    "eai_again",
    "connection refused",
    "connection reset",
    "connection aborted",
    "network is unreachable",
    "no route to host",
    "name resolution",
    "temporary failure in name resolution",
    "failed to establish a new connection",
    "max retries exceeded",
    "broken pipe",
    "network unreachable",
    "socket hang up",
    "tls handshake",
    "ssl:",
)
_SKIP_MARKERS = (
    "azure sign-in timed out",
    "session expired",
    "start again",
    "unknown submit session",
    "no longer active",
    "finish azure sign-in",
    "enter a name",
    "name tag must be",
    "cancelled",
)

_buses: dict[str, list[asyncio.Queue[dict | None]]] = {}
_bus_guard = asyncio.Lock()
_login_tasks: dict[str, asyncio.Task] = {}
_commit_tasks: dict[str, asyncio.Task] = {}
_approve_tasks: dict[int, asyncio.Task] = {}
_retry_tasks: dict[int, asyncio.Task] = {}
_aborted_sessions: set[str] = set()
_aborted_approvals: set[int] = set()
AUTO_RETRY_DELAY_SECONDS = 20
AUTO_RETRY_LIMIT = 6


class SubmitError(RuntimeError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ttl_seconds() -> int:
    return max(300, int(get_settings().submit_session_ttl_seconds))


def public_snapshot(row: SpSubmitRequest, message: str | None = None) -> SubmitSessionSnapshot:
    subs: list[SubmitSubscription] = []
    if row.subscriptions_json:
        try:
            raw = json.loads(row.subscriptions_json)
        except json.JSONDecodeError:
            raw = []
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                sid = str(item.get("subscription_id") or "").strip()
                if not sid:
                    continue
                subs.append(
                    SubmitSubscription(
                        subscription_id=sid,
                        name=str(item.get("name") or ""),
                        tenant_id=str(item.get("tenant_id") or ""),
                        is_default=bool(item.get("is_default")),
                    )
                )
    return SubmitSessionSnapshot(
        session_id=row.session_id,
        status=row.status,
        account_holder=row.account_holder,
        person_associated=row.person_associated,
        group_tag=normalize_group(row.group_tag),
        subscription_id=row.subscription_id,
        subscription_name=row.subscription_name,
        device_user_code=row.device_user_code,
        device_verification_uri=row.device_verification_uri,
        subscriptions=subs,
        error=row.error_message,
        billing_error=row.billing_error,
        message=message,
    )


def pending_public(row: SpSubmitRequest) -> PendingRequestPublic:
    return PendingRequestPublic(
        id=row.id,
        status=row.status,
        person_associated=row.person_associated,
        group_tag=normalize_group(row.group_tag),
        account_holder=row.account_holder,
        name=row.name,
        subscription_id=row.subscription_id,
        subscription_name=row.subscription_name,
        tenant_id=row.tenant_id,
        billing_error=row.billing_error,
        error_message=row.error_message,
        error_kind=row.error_kind,
        created_at=row.created_at,
        updated_at=row.updated_at,
        approved_at=row.approved_at,
        rejected_at=row.rejected_at,
        **public_grant_fields(row),
        can_retry_deploy=bool(
            row.client_secret_encrypted
            and row.client_id
            and row.tenant_id
            and row.subscription_id
            and (
                row.status == STATUS_PENDING
                or (row.status == STATUS_FAILED and row.error_kind == ERROR_KIND_DEPLOY)
            )
        ),
    )


def _subscriptions_from_az(accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in accounts:
        state = str(item.get("state") or "Enabled")
        if state.lower() not in {"enabled", "warned", ""}:
            continue
        sid = str(item.get("id") or "").strip()
        if not sid or is_tenant_level_account(item):
            continue
        out.append(
            {
                "subscription_id": sid,
                "name": str(item.get("name") or ""),
                "tenant_id": str(item.get("tenantId") or ""),
                "is_default": bool(item.get("isDefault")),
            }
        )
    out.sort(key=lambda row: (not row["is_default"], (row["name"] or "").lower()))
    return out


async def _publish(session_id: str, event: dict[str, Any]) -> None:
    sid = (session_id or "").strip().lower()
    payload = {**event, "session_id": sid}
    async with _bus_guard:
        queues = list(_buses.get(sid, []))
    for queue in queues:
        await queue.put(payload)


async def subscribe(session_id: str) -> asyncio.Queue[dict | None]:
    sid = (session_id or "").strip().lower()
    queue: asyncio.Queue[dict | None] = asyncio.Queue()
    async with _bus_guard:
        _buses.setdefault(sid, []).append(queue)
    return queue


async def unsubscribe(session_id: str, queue: asyncio.Queue[dict | None]) -> None:
    sid = (session_id or "").strip().lower()
    async with _bus_guard:
        holders = _buses.get(sid) or []
        if queue in holders:
            holders.remove(queue)
        if not holders:
            _buses.pop(sid, None)


def _sid(session_id: str | None) -> str:
    return (session_id or "").strip().lower()


def register_commit_task(session_id: str, task: asyncio.Task) -> None:
    sid = _sid(session_id)
    _aborted_sessions.discard(sid)
    _commit_tasks[sid] = task
    task.add_done_callback(lambda _t, key=sid: _commit_tasks.pop(key, None))


def register_approve_task(request_id: int, task: asyncio.Task) -> None:
    _aborted_approvals.discard(request_id)
    _approve_tasks[request_id] = task
    task.add_done_callback(lambda _t, key=request_id: _approve_tasks.pop(key, None))


def register_retry_task(request_id: int, task: asyncio.Task) -> None:
    previous = _retry_tasks.pop(request_id, None)
    if previous is not None and not previous.done():
        previous.cancel()
    _retry_tasks[request_id] = task
    task.add_done_callback(lambda _t, key=request_id: _retry_tasks.pop(key, None))


def _session_has_work(session_id: str | None) -> bool:
    sid = _sid(session_id)
    login = _login_tasks.get(sid)
    commit = _commit_tasks.get(sid)
    return (login is not None and not login.done()) or (commit is not None and not commit.done())


def _approve_has_work(request_id: int) -> bool:
    task = _approve_tasks.get(request_id)
    return task is not None and not task.done()


def _retry_has_work(request_id: int) -> bool:
    task = _retry_tasks.get(request_id)
    return task is not None and not task.done()


def session_aborted(session_id: str | None) -> bool:
    return _sid(session_id) in _aborted_sessions


def approve_aborted(request_id: int) -> bool:
    return request_id in _aborted_approvals


def _cancel_task(task: asyncio.Task | None) -> None:
    if task is not None and not task.done():
        task.cancel()


async def abort_session_work(session_id: str | None) -> None:
    sid = _sid(session_id)
    if not sid:
        return
    _aborted_sessions.add(sid)
    _cancel_task(_login_tasks.pop(sid, None))
    _cancel_task(_commit_tasks.pop(sid, None))
    await drop_az_session(sid)


async def abort_approve_work(request_id: int) -> None:
    _aborted_approvals.add(request_id)
    _cancel_task(_approve_tasks.pop(request_id, None))
    _cancel_task(_retry_tasks.pop(request_id, None))


async def get_request(db: AsyncSession, session_id: str) -> SpSubmitRequest | None:
    result = await db.execute(select(SpSubmitRequest).where(SpSubmitRequest.session_id == session_id))
    return result.scalar_one_or_none()


async def get_request_by_id(db: AsyncSession, request_id: int) -> SpSubmitRequest | None:
    result = await db.execute(select(SpSubmitRequest).where(SpSubmitRequest.id == request_id))
    return result.scalar_one_or_none()


async def live_request_for_subscription(
    db: AsyncSession,
    subscription_id: str,
    exclude_id: int | None = None,
) -> SpSubmitRequest | None:
    wanted = subscription_id.strip().lower()
    if not wanted:
        return None
    stmt = select(SpSubmitRequest).where(
        func.lower(SpSubmitRequest.subscription_id) == wanted,
        SpSubmitRequest.status.in_(LIVE_SUB_STATUSES),
    )
    if exclude_id is not None:
        stmt = stmt.where(SpSubmitRequest.id != exclude_id)
    return (await db.execute(stmt)).scalars().first()


async def _discard_failed_for_subscription(
    db: AsyncSession,
    subscription_id: str,
    exclude_id: int | None = None,
    inherit_into: SpSubmitRequest | None = None,
) -> None:
    wanted = subscription_id.strip().lower()
    if not wanted:
        return
    stmt = select(SpSubmitRequest).where(
        func.lower(SpSubmitRequest.subscription_id) == wanted,
        SpSubmitRequest.status == STATUS_FAILED,
    )
    if exclude_id is not None:
        stmt = stmt.where(SpSubmitRequest.id != exclude_id)
    for old in (await db.execute(stmt)).scalars():
        if (
            inherit_into is not None
            and old.client_secret_encrypted
            and not inherit_into.client_secret_encrypted
        ):
            inherit_into.client_id = old.client_id
            inherit_into.client_secret_encrypted = old.client_secret_encrypted
            inherit_into.sp_display_name = old.sp_display_name or inherit_into.sp_display_name
        await abort_approve_work(old.id)
        await drop_az_session(old.session_id)
        await db.delete(old)


async def list_owner_names(db: AsyncSession, group: str | None = None) -> list[str]:
    return await list_join_picker_names(db, group)


def _wipe_secret(row: SpSubmitRequest) -> None:
    row.client_secret_encrypted = None


def classify_submit_error(message: str) -> str | None:
    """Return 'account' to keep in the portal, None to drop (network / user abort)."""
    text = (message or "").strip().lower()
    if not text:
        return None
    if any(marker in text for marker in _SKIP_MARKERS):
        return None
    if any(marker in text for marker in _NETWORK_MARKERS):
        if "authorization" in text or "aadsts" in text or "forbidden" in text:
            return ERROR_KIND_ACCOUNT
        return None
    return ERROR_KIND_ACCOUNT


async def apply_submit_failure(
    db: AsyncSession,
    row: SpSubmitRequest,
    message: str,
    *,
    keep_network: bool = False,
    error_kind: str | None = None,
) -> str | None:
    kind = error_kind or classify_submit_error(message)
    detail = scrub_az_text(message)[:800]
    session_id = row.session_id
    has_identity = bool(row.client_secret_encrypted)
    keep_kinds = {ERROR_KIND_ACCOUNT, ERROR_KIND_ROLES, ERROR_KIND_DEPLOY}
    if kind not in keep_kinds and not keep_network and not has_identity:
        await drop_az_session(session_id)
        await db.delete(row)
        await db.commit()
        return None
    row.status = STATUS_FAILED
    row.error_kind = kind or ERROR_KIND_ACCOUNT
    row.error_message = detail or "Submission failed."
    if not has_identity:
        _wipe_secret(row)
    row.az_config_dir = None
    await db.commit()
    await drop_az_session(session_id)
    return row.error_kind


def _row_age(row: SpSubmitRequest, now: datetime) -> timedelta:
    stamp = row.updated_at or row.created_at or now
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return now - stamp


def _az_config_missing(row: SpSubmitRequest) -> bool:
    path = (row.az_config_dir or "").strip()
    if not path:
        return True
    return not Path(path).is_dir()


async def _expire_open_login(row: SpSubmitRequest, message: str) -> None:
    _wipe_secret(row)
    row.status = STATUS_EXPIRED
    row.error_message = message
    await abort_session_work(row.session_id)
    row.az_config_dir = None
    await _publish(row.session_id, {"type": "error", "detail": message})


async def expire_stale(db: AsyncSession, *, orphan_open: bool = False) -> int:
    now = _utcnow()
    login_cutoff = now - timedelta(seconds=_ttl_seconds())
    from app.services.join_enrollee_service import auto_approve_settings

    auto_approve = await auto_approve_settings(db)
    rows = (
        await db.execute(
            select(SpSubmitRequest).where(
                SpSubmitRequest.status.in_(
                    (*tuple(OPEN_LOGIN), STATUS_CREATING_SP, STATUS_APPROVING)
                )
            )
        )
    ).scalars()
    count = 0
    for row in rows:
        if row.status == STATUS_CREATING_SP:
            if _session_has_work(row.session_id):
                continue
            if not orphan_open and _row_age(row, now) < CREATING_SP_MAX_AGE:
                continue
            await abort_session_work(row.session_id)
            await apply_submit_failure(
                db,
                row,
                "Interrupted. The server restarted or identity setup timed out. Sign in again at /join.",
                keep_network=True,
                error_kind=ERROR_KIND_ROLES,
            )
            await _publish(row.session_id, {"type": "error", "detail": row.error_message or "Interrupted."})
            count += 1
            continue
        if row.status == STATUS_APPROVING:
            if _approve_has_work(row.id):
                continue
            if not orphan_open and _row_age(row, now) < APPROVING_MAX_AGE:
                continue
            expired_id = row.id
            await abort_approve_work(expired_id)
            await apply_submit_failure(
                db,
                row,
                "Interrupted. The server restarted or deploy timed out. Use Retry deploy.",
                keep_network=True,
                error_kind=ERROR_KIND_DEPLOY,
            )
            if auto_approve[normalize_group(row.group_tag)]:
                _aborted_approvals.discard(expired_id)
                schedule_auto_retry(expired_id)
            count += 1
            continue
        if row.status == STATUS_LOGIN_STARTED:
            live = _session_has_work(row.session_id)
            if live and not orphan_open:
                continue
            if not orphan_open and _row_age(row, now) < LOGIN_TASK_GRACE:
                continue
            await _expire_open_login(row, OPEN_LOGIN_INTERRUPTED)
            count += 1
            continue
        if row.status == STATUS_LOGGED_IN:
            stale = row.updated_at is None or row.updated_at < login_cutoff
            if orphan_open or _az_config_missing(row) or stale:
                await _expire_open_login(
                    row,
                    OPEN_LOGIN_INTERRUPTED if (orphan_open or _az_config_missing(row)) else "Session expired. Start again.",
                )
                count += 1
            continue
    if count:
        await db.commit()
    return count


def parse_submit_tenant_id(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    tid = normalize_tenant_id(text)
    if not tid:
        raise SubmitError(
            "Directory (tenant) ID must be a GUID from Azure Portal → Microsoft Entra ID → Overview."
        )
    return tid


async def create_session(db: AsyncSession, tenant_id: str | None = None) -> SpSubmitRequest:
    await expire_stale(db)
    if not shutil.which("az"):
        raise SubmitError("Azure CLI (az) is not on the backend PATH.")
    session_id = str(uuid.uuid4())
    tid = parse_submit_tenant_id(tenant_id)
    az = await get_az_session(session_id)
    row = SpSubmitRequest(
        session_id=session_id,
        status=STATUS_LOGIN_STARTED,
        az_config_dir=str(az.config_dir),
        tenant_id=tid,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    task = asyncio.create_task(_run_login(session_id, tenant_id=tid))
    _login_tasks[session_id.lower()] = task
    task.add_done_callback(lambda _t, sid=session_id.lower(): _login_tasks.pop(sid, None))
    return row


async def _run_login(session_id: str, tenant_id: str | None = None) -> None:
    from app.database import SessionLocal

    async def on_event(event: dict[str, Any]) -> None:
        if event.get("type") == "device_code":
            async with SessionLocal() as db:
                row = await get_request(db, session_id)
                if row is None:
                    return
                code = str(event.get("user_code") or "").strip()[:32]
                uri = str(event.get("verification_uri") or "").strip()[:256]
                if code:
                    row.device_user_code = code
                if uri:
                    row.device_verification_uri = uri
                await db.commit()
        elif event.get("type") == "device_code_wait":
            async with SessionLocal() as db:
                row = await get_request(db, session_id)
                if row is not None:
                    row.device_user_code = None
                    await db.commit()
        await _publish(session_id, event)

    try:
        az = await get_az_session(session_id)
        az.set_log(on_event)
        accounts = await az.device_login(on_event, tenant_id=tenant_id)
        identity = await az.account_show()
        user = identity.get("user") or {}
        email = str(user.get("name") or "").strip() or None
        subs = _subscriptions_from_az(accounts)
        async with SessionLocal() as db:
            row = await get_request(db, session_id)
            if row is None or row.status not in {STATUS_LOGIN_STARTED, STATUS_LOGGED_IN}:
                return
            row.account_holder = email
            row.tenant_id = str(identity.get("tenantId") or "") or row.tenant_id
            if not subs:
                row.status = STATUS_FAILED
                row.error_kind = ERROR_KIND_ACCOUNT
                row.error_message = "No Azure subscription was found on this Microsoft account."
                await db.commit()
                await drop_az_session(session_id)
                await _publish(
                    session_id,
                    {"type": "error", "detail": "No Azure subscription was found on this Microsoft account."},
                )
                return
            row.status = STATUS_LOGGED_IN
            row.subscriptions_json = json.dumps(subs)
            row.error_message = None
            row.error_kind = None
            await db.commit()
            await db.refresh(row)
            snap = public_snapshot(row, message="Signed in. Pick a subscription and a name.")
        await _publish(
            session_id,
            {
                "type": "logged_in",
                "account_holder": email,
                "subscriptions": [item.model_dump() for item in snap.subscriptions],
                "message": snap.message,
            },
        )
    except asyncio.CancelledError:
        raise
    except AzCliError as exc:
        identity: dict = {}
        try:
            identity = await az.account_show()
        except Exception:  # noqa: BLE001
            identity = {}
        email = str((identity.get("user") or {}).get("name") or "").strip() or None
        tenant = str(identity.get("tenantId") or "").strip() or None
        async with SessionLocal() as db:
            row = await get_request(db, session_id)
            if row is not None:
                if email and not row.account_holder:
                    row.account_holder = email
                if tenant and not row.tenant_id:
                    row.tenant_id = tenant
                await apply_submit_failure(db, row, str(exc))
        await _publish(session_id, {"type": "error", "detail": str(exc)})
    except Exception as exc:  # noqa: BLE001
        logger.exception("Submit login failed")
        detail = scrub_az_text(str(exc))[:400]
        async with SessionLocal() as db:
            row = await get_request(db, session_id)
            if row is not None:
                await apply_submit_failure(db, row, detail)
        await _publish(session_id, {"type": "error", "detail": detail})


async def allocate_submit_name(db: AsyncSession, preferred: str, exclude_id: int) -> str:
    """Claim a portal account name that no account or submission holds yet.

    Takes a transaction-scoped advisory lock first. Without it two concurrent
    commits read the same taken-set and allocate the same name; the portal
    would later rename one to Lioxi-Ayush1 while its Azure stack had already
    been built from Lioxi-Ayush, leaving the two permanently disagreeing.
    The lock is released when the caller's transaction ends.
    """
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": NAME_ALLOCATION_LOCK})
    taken = {
        str(name).lower()
        for (name,) in (await db.execute(select(ProviderAccount.name))).all()
        if name
    }
    taken.update(
        str(name).lower()
        for (name,) in (
            await db.execute(select(SpSubmitRequest.name).where(SpSubmitRequest.id != exclude_id))
        ).all()
        if name
    )
    return allocate_unique_name(preferred, taken)


async def commit_session(
    db: AsyncSession,
    session_id: str,
    subscription_id: str,
    person_associated: str,
    group_tag: str | None = None,
    on_progress: ProgressFn | None = None,
) -> SpSubmitRequest:
    await expire_stale(db)
    row = (
        await db.execute(
            select(SpSubmitRequest).where(SpSubmitRequest.session_id == session_id).with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise SubmitError("Unknown submit session.")
    if row.status == STATUS_CREATING_SP:
        raise SubmitError("This sign-in is already submitting.")
    if row.status == STATUS_PENDING:
        raise SubmitError("This session is already waiting for admin approval.")
    if row.status == STATUS_FAILED:
        raise SubmitError("This attempt failed. Start Azure sign-in again to reapply.")
    if row.status in TERMINAL:
        raise SubmitError("This session is no longer active. Start again.")
    if row.status != STATUS_LOGGED_IN:
        raise SubmitError("Finish Azure sign-in before submitting.")

    group = normalize_group(group_tag)
    try:
        await validate_enrollee_for_submit(db, person_associated, group)
    except EnrolleeError as exc:
        raise SubmitError(str(exc)) from exc

    wanted = subscription_id.strip().lower()
    snap = public_snapshot(row)
    match = next((item for item in snap.subscriptions if item.subscription_id.lower() == wanted), None)
    if match is None:
        raise SubmitError("That subscription is not on this Azure login.")
    if is_tenant_level_account(
        subscription_id=match.subscription_id,
        tenant_id=match.tenant_id,
        name=match.name,
    ):
        raise SubmitError(
            "This Microsoft account has no Azure subscription (tenant-level login only). "
            "Sign in with the account that owns the subscription at portal.azure.com."
        )

    wanted_sub = match.subscription_id.strip().lower()
    existing_account = (
        await db.execute(
            select(ProviderAccount.id).where(func.lower(ProviderAccount.subscription_id) == wanted_sub).limit(1)
        )
    ).scalar_one_or_none()
    if existing_account is not None:
        raise SubmitError(ALREADY_ONBOARDED_MESSAGE)

    existing_pending = await live_request_for_subscription(db, match.subscription_id, exclude_id=row.id)
    if existing_pending is not None:
        raise SubmitError(DUPLICATE_SUB_MESSAGE)
    await _discard_failed_for_subscription(db, match.subscription_id, exclude_id=row.id, inherit_into=row)

    try:
        enrollee = await ensure_enrollee_for_submit(db, person_associated, group)
    except EnrolleeError as exc:
        raise SubmitError(str(exc)) from exc
    person = enrollee.name

    mod = load_deploy_module()
    slug = mod.slugify(person)
    label = "".join(person.split())
    # The picked name is the owner tag for both groups. Only the portal
    # account label differs: Lioxi-<Name> for SB, the bare enrolled name for
    # VCS (which then drives the Azure <slug>-proxy stack names).
    preferred = join_account_name(group, row.account_holder, f"Lioxi-{label or slug}", person)
    display_name = await allocate_submit_name(db, preferred, exclude_id=row.id)
    row.status = STATUS_CREATING_SP
    row.group_tag = group
    row.person_associated = person
    row.name = display_name
    row.subscription_id = match.subscription_id
    row.subscription_name = match.name or row.subscription_name
    row.tenant_id = match.tenant_id or row.tenant_id
    row.error_message = None
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if _is_duplicate_name_error(exc):
            raise SubmitError(
                "That account name was just taken. Submit again to get the next one."
            ) from exc
        if _is_duplicate_enrollee_error(exc):
            raise SubmitError("That name was just added. Submit again.") from exc
        raise SubmitError(DUPLICATE_SUB_MESSAGE) from exc

    async def emit(event: dict[str, Any]) -> None:
        payload = {**event, "session_id": session_id}
        await _publish(session_id, payload)
        if on_progress is not None:
            await on_progress(payload)

    await emit(
        {
            "type": "phase",
            "phase": "sp",
            "message": "Creating monitor identity…",
            "done": 0,
            "total": _grant_steps()[2],
        }
    )
    try:
        await _provision_sp(db, row, slug, emit)
    except asyncio.CancelledError:
        try:
            await db.refresh(row)
            if row.status == STATUS_CREATING_SP:
                await apply_submit_failure(db, row, "Cancelled.", keep_network=True)
                await emit({"type": "error", "detail": "Cancelled."})
        except Exception:
            logger.debug("submit cancel already applied session=%s", session_id, exc_info=True)
        raise
    except (AzCliError, SubmitError) as exc:
        kind = ERROR_KIND_ROLES if "required admin roles" in str(exc).lower() else None
        await apply_submit_failure(db, row, str(exc), keep_network=True, error_kind=kind)
        await emit({"type": "error", "detail": str(exc)})
        raise SubmitError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        detail = scrub_az_text(str(exc))[:400]
        await apply_submit_failure(db, row, detail, keep_network=True)
        await emit({"type": "error", "detail": detail})
        raise SubmitError(detail) from exc

    await db.refresh(row)
    if row.status != STATUS_PENDING:
        return row
    from app.services.join_enrollee_service import is_auto_approve_enabled

    if await is_auto_approve_enabled(db, row.group_tag):
        try:
            await enqueue_approve(db, row.id, authorized_by=AUTH_AUTO)
            await db.refresh(row)
            await emit(
                {
                    "type": "done",
                    "status": row.status,
                    "message": "Submitted. Kimi K3 deploy is starting automatically.",
                }
            )
            return row
        except SubmitError as exc:
            logger.info("Auto-approve could not start request=%s: %s", row.id, exc)
    await emit(
        {
            "type": "done",
            "status": STATUS_PENDING,
            "message": "Submitted. An admin will deploy Kimi K3.",
        }
    )
    return row


async def _verify_sp_secret(tenant_id: str, client_id: str, secret: str) -> str:
    tid = (tenant_id or "").strip()
    cid = (client_id or "").strip()
    pwd = (secret or "").strip()
    if not tid or not cid or not pwd:
        return "invalid"
    url = f"https://login.microsoftonline.com/{tid}/oauth2/v2.0/token"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                url,
                data={
                    "client_id": cid,
                    "client_secret": pwd,
                    "grant_type": "client_credentials",
                    "scope": _TOKEN_SCOPE,
                },
            )
    except httpx.HTTPError:
        return "unknown"
    if response.status_code == 200:
        return "ok"
    body = (response.text or "").lower()
    if response.status_code in {400, 401} and any(marker in body for marker in _INVALID_SECRET_MARKERS):
        return "invalid"
    return "unknown"


async def _ensure_still_creating(db: AsyncSession, row: SpSubmitRequest) -> None:
    if session_aborted(row.session_id):
        raise asyncio.CancelledError
    await db.refresh(row)
    if row.status != STATUS_CREATING_SP:
        raise asyncio.CancelledError


async def _provision_sp(
    db: AsyncSession,
    row: SpSubmitRequest,
    slug: str,
    emit: ProgressFn,
) -> None:
    az = await get_az_session(row.session_id, row.az_config_dir)
    az.set_log(emit)
    sub = str(row.subscription_id or "")
    if is_tenant_level_account(
        subscription_id=sub,
        tenant_id=str(row.tenant_id or ""),
        name=str(row.subscription_name or ""),
    ):
        raise SubmitError(
            "This Microsoft account has no Azure subscription (tenant-level login only). "
            "Ask them to join again with the account that owns the subscription."
        )

    roles, admin_roles, total_grants = _grant_steps()

    async def emit_progress(message: str, done: int, role: str | None = None) -> None:
        event: dict[str, Any] = {
            "type": "phase",
            "phase": "roles" if done else "sp",
            "message": message,
            "done": done,
            "total": total_grants,
        }
        if role is not None:
            event["role"] = role
        await emit(event)

    await emit_progress("Creating monitor identity…", 0)
    await az.set_subscription(sub)

    stored = (
        await db.execute(
            select(AzureServicePrincipal).where(func.lower(AzureServicePrincipal.subscription_id) == sub.strip().lower())
        )
    ).scalar_one_or_none()
    box = get_secret_box()
    secret: str | None = None
    app_id: str | None = None
    sp_name = SP_NAME
    reused = False

    if row.client_id and row.client_secret_encrypted:
        app_id = row.client_id
        secret = box.decrypt(row.client_secret_encrypted)
        sp_name = row.sp_display_name or SP_NAME
        reused = True
        await emit({"type": "phase", "phase": "sp", "message": "Reusing this subscription’s monitor identity…"})
    elif stored is not None:
        app_id = stored.client_id
        secret = box.decrypt(stored.client_secret_encrypted)
        sp_name = stored.name or SP_NAME
        reused = True
        await emit({"type": "phase", "phase": "sp", "message": "Reusing this subscription’s monitor identity…"})
    else:
        app_id, secret, sp_name, reused = await _create_or_name_sp(az, db, sub, slug, emit)

    if reused:
        verdict = await _verify_sp_secret(str(row.tenant_id or ""), str(app_id or ""), str(secret or ""))
        if verdict == "invalid":
            await emit({"type": "phase", "phase": "sp", "message": "Stored secret was rejected by Microsoft. Rotating this app only…"})
            secret = await az.reset_sp_password(app_id)
        elif verdict == "unknown":
            await emit(
                {
                    "type": "phase",
                    "phase": "sp",
                    "message": "Could not verify the stored secret (network). Reusing it without rotating.",
                }
            )

    await _ensure_still_creating(db, row)
    row.client_id = app_id
    row.client_secret_encrypted = box.encrypt(secret)
    row.sp_display_name = sp_name
    await db.commit()

    await emit_progress("Monitor identity ready.", 1)
    oid = await az.sp_object_id(app_id)
    if stored is None and oid:
        await az.add_sp_as_app_owner(app_id, oid)
    tenant = str(row.tenant_id or "")
    assigns_billing = bool(oid and tenant)

    failed_by_role: dict[str, str] = {}
    for index, role in enumerate(roles):
        if session_aborted(row.session_id):
            raise asyncio.CancelledError
        await emit_progress(f"Assigning {role}…", index + 1, role)
        ok, err = await az.assign_role(app_id, role, sub, object_id=oid, timeout=90 if role == "Contributor" else 45)
        if not ok:
            failed_by_role[role] = err or "assignment failed"
        await emit_progress(f"Assigning {role}…", index + 2, role)

    admin_failed = [f"{role}: {err}" for role, err in failed_by_role.items() if role in admin_roles]
    if admin_failed:
        raise SubmitError(
            "Could not assign required admin roles on this subscription. " + "; ".join(admin_failed)[:800]
        )

    billing_err = None
    if assigns_billing:
        await emit_progress("Assigning billing reader…", total_grants - 1)
        ok, err = await az.assign_billing_reader(oid, tenant)
        if not ok:
            billing_err = err
    await emit_progress("Finishing…", total_grants)

    await _ensure_still_creating(db, row)
    row.billing_error = billing_err
    row.status = STATUS_PENDING
    viewer_failed = [f"{role}: {err}" for role, err in failed_by_role.items() if role not in admin_roles]
    if viewer_failed:
        row.error_message = "Some roles could not be assigned: " + "; ".join(viewer_failed)[:800]
    await db.commit()
    await drop_az_session(row.session_id)
    row.az_config_dir = None
    await db.commit()


async def _create_or_name_sp(
    az,
    db: AsyncSession,
    subscription_id: str,
    slug: str,
    emit: ProgressFn,
) -> tuple[str, str, str, bool]:
    wanted_sub = (subscription_id or "").strip().lower()
    box = get_secret_box()

    async def take(name: str) -> tuple[str, str, str, bool] | None:
        existing = await az.list_sps_by_name(name)
        if not existing:
            await emit({"type": "phase", "phase": "sp", "message": f"Creating {name}…"})
            app_id, secret = await az.create_sp(name)
            return app_id, secret, name, False
        app_id = str(existing[0].get("appId") or "").strip()
        if not app_id:
            return None
        sp_row = (
            await db.execute(
                select(AzureServicePrincipal).where(func.lower(AzureServicePrincipal.client_id) == app_id.lower())
            )
        ).scalars().first()
        if sp_row is not None:
            sp_sub = (sp_row.subscription_id or "").strip().lower()
            if sp_sub != wanted_sub:
                return None
            secret = box.decrypt(sp_row.client_secret_encrypted)
            await emit({"type": "phase", "phase": "sp", "message": "Reusing this subscription’s monitor identity…"})
            return app_id, secret, sp_row.name or name, True
        pending_row = (
            await db.execute(
                select(SpSubmitRequest).where(
                    func.lower(SpSubmitRequest.client_id) == app_id.lower(),
                    SpSubmitRequest.client_secret_encrypted.is_not(None),
                )
            )
        ).scalars().first()
        if pending_row is None:
            return None
        pending_sub = (pending_row.subscription_id or "").strip().lower()
        if pending_sub != wanted_sub:
            return None
        secret = box.decrypt(pending_row.client_secret_encrypted)
        await emit({"type": "phase", "phase": "sp", "message": "Reusing this subscription’s monitor identity…"})
        return app_id, secret, pending_row.sp_display_name or name, True

    taken = await take(SP_NAME)
    if taken:
        return taken
    named = f"{SP_NAME}-{slug}"[:120]
    taken = await take(named)
    if taken:
        return taken
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    fallback = f"{SP_NAME}-{slug}-{suffix}"[:120]
    await emit({"type": "phase", "phase": "sp", "message": f"Creating {fallback}…"})
    app_id, secret = await az.create_sp(fallback)
    return app_id, secret, fallback, False


def deploy_payload_from_row(row: SpSubmitRequest, secret: str) -> dict[str, str]:
    return {
        "name": row.name or row.person_associated or "account",
        "group_tag": normalize_group(row.group_tag),
        "account_holder": row.account_holder or "",
        "person_associated": row.person_associated or "",
        "AZURE_TENANT_ID": row.tenant_id or "",
        "AZURE_CLIENT_ID": row.client_id or "",
        "AZURE_CLIENT_SECRET": secret,
        "AZURE_SUBSCRIPTION_ID": row.subscription_id or "",
        "subscription_name": row.subscription_name or "",
    }


async def list_pending(db: AsyncSession) -> list[SpSubmitRequest]:
    await expire_stale(db)
    cutoff = _utcnow() - timedelta(days=2)
    rows = (
        await db.execute(
            select(SpSubmitRequest)
            .where(
                    or_(
                        SpSubmitRequest.status.in_(
                            (STATUS_PENDING, STATUS_CREATING_SP, STATUS_APPROVING)
                        ),
                        and_(
                            SpSubmitRequest.status == STATUS_FAILED,
                            or_(
                                SpSubmitRequest.error_kind.is_(None),
                                SpSubmitRequest.error_kind.in_(
                                    (ERROR_KIND_ACCOUNT, ERROR_KIND_ROLES, ERROR_KIND_DEPLOY)
                                ),
                            ),
                        ),
                        and_(
                            SpSubmitRequest.status == STATUS_APPROVED,
                            SpSubmitRequest.updated_at >= cutoff,
                        ),
                    ),
            )
            .order_by(SpSubmitRequest.created_at.desc())
        )
    ).scalars()
    return list(rows)


def _missing_subscription_error(text: str) -> bool:
    lowered = (text or "").lower()
    return "subscriptionnotfound" in lowered or (
        "subscription" in lowered and "could not be found" in lowered
    )


def _row_is_tenant_level(row: SpSubmitRequest) -> bool:
    return is_tenant_level_account(
        subscription_id=str(row.subscription_id or ""),
        tenant_id=str(row.tenant_id or ""),
        name=str(row.subscription_name or ""),
    )


async def reject_request(db: AsyncSession, request_id: int) -> tuple[int, str | None]:
    """Decline a submission: wipe the secret, drop az state, and delete the row so they can register again."""
    row = await get_request_by_id(db, request_id)
    if row is None:
        raise SubmitError("Unknown pending request.")
    if row.status not in {
        STATUS_PENDING,
        STATUS_FAILED,
        STATUS_LOGIN_STARTED,
        STATUS_LOGGED_IN,
        STATUS_CREATING_SP,
    }:
        raise SubmitError("This request cannot be declined.")
    deleted_id = row.id
    subscription_id = row.subscription_id
    session_id = row.session_id
    await abort_approve_work(deleted_id)
    await abort_session_work(session_id)
    leftover_error: str | None = None
    should_clean_azure = (
        bool(row.client_secret_encrypted and row.client_id and row.tenant_id and row.subscription_id)
        and not _row_is_tenant_level(row)
    )
    if should_clean_azure:
        try:
            from app.services.kimi_deploy_service import delete_accounts

            secret = get_secret_box().decrypt(row.client_secret_encrypted)
            results = await delete_accounts([deploy_payload_from_row(row, secret)], jobs=1)
            if results and not results[0].ok:
                leftover_error = results[0].error or "Azure leftover delete did not complete."
        except Exception as exc:
            leftover_error = str(exc)[:400]
            logger.exception("Could not remove leftover Kimi stack for submit %s", deleted_id)
        if (
            leftover_error
            and row.error_kind == ERROR_KIND_DEPLOY
            and not _missing_subscription_error(leftover_error)
        ):
            raise SubmitError(
                "Could not remove leftover Azure Kimi resources. The card is still here — try Clear leftover again. "
                + leftover_error
            )
    from app.services.service_principal_store import drop_orphan_service_principal

    await drop_orphan_service_principal(db, subscription_id)
    _wipe_secret(row)
    await drop_az_session(session_id)
    await db.delete(row)
    await db.commit()
    await _publish(session_id, {"type": "error", "detail": "An admin declined this request. You can register again."})
    return deleted_id, subscription_id


def _auto_retry_count(row: SpSubmitRequest | None) -> int:
    if row is None:
        return 0
    try:
        return max(0, int(getattr(row, "auto_retry_count", 0) or 0))
    except (TypeError, ValueError):
        return 0


def _row_can_auto_retry(row: SpSubmitRequest | None) -> bool:
    if row is None or row.status != STATUS_FAILED or row.error_kind != ERROR_KIND_DEPLOY:
        return False
    if _auto_retry_count(row) >= AUTO_RETRY_LIMIT:
        return False
    return bool(row.client_secret_encrypted and row.client_id and row.subscription_id and row.tenant_id)


def _can_approve_row(row: SpSubmitRequest, *, retry: bool) -> str | None:
    if row.status == STATUS_APPROVING or _approve_has_work(row.id):
        return "This submission is already being approved."
    if retry:
        if row.status != STATUS_FAILED or row.error_kind != ERROR_KIND_DEPLOY:
            return "This failure cannot be retried with Retry deploy. Ask them to join again."
    elif row.status != STATUS_PENDING:
        return "Only pending submissions can be approved."
    if not row.client_secret_encrypted or not row.client_id or not row.subscription_id or not row.tenant_id:
        return "This request is missing a stored identity. Ask the user to submit again."
    return None


async def enqueue_approve(
    db: AsyncSession,
    request_id: int,
    *,
    retry: bool = False,
    auto_retry: bool = False,
    authorized_by: str = AUTH_ADMIN,
    new_api_priority: int | None = None,
    new_api_weight: int | None = None,
) -> SpSubmitRequest:
    row = (
        await db.execute(select(SpSubmitRequest).where(SpSubmitRequest.id == request_id).with_for_update())
    ).scalar_one_or_none()
    if row is None:
        raise SubmitError("Unknown pending request.")
    problem = _can_approve_row(row, retry=retry or row.status == STATUS_FAILED)
    if problem:
        raise SubmitError(problem)
    if auto_retry:
        if _auto_retry_count(row) >= AUTO_RETRY_LIMIT:
            raise SubmitError("Auto-retry limit reached.")
        row.auto_retry_count = _auto_retry_count(row) + 1
    else:
        row.auto_retry_count = 0
    priority, weight = await resolve_routing(db, new_api_priority, new_api_weight)
    row.status = STATUS_APPROVING
    row.error_message = None
    await db.commit()
    task = asyncio.create_task(_run_approve_job(request_id, priority, weight, authorized_by))
    register_approve_task(request_id, task)
    return row


async def enqueue_approve_many(
    db: AsyncSession,
    ids: list[int] | None,
    *,
    retry: bool,
    auto_retry: bool = False,
    authorized_by: str = AUTH_ADMIN,
    new_api_priority: int | None = None,
    new_api_weight: int | None = None,
    group: str | None = None,
) -> tuple[list[int], list[tuple[int, str]]]:
    rows = await list_pending(db)
    wanted = {int(item) for item in ids} if ids else None
    only_group = normalize_group(group) if group is not None else None
    started: list[int] = []
    skipped: list[tuple[int, str]] = []
    for row in rows:
        if wanted is not None and row.id not in wanted:
            continue
        if only_group is not None and normalize_group(row.group_tag) != only_group:
            continue
        if retry:
            if row.status != STATUS_FAILED or row.error_kind != ERROR_KIND_DEPLOY:
                if wanted is not None:
                    skipped.append((row.id, "This failure cannot be retried with Retry deploy."))
                continue
            if auto_retry and not _row_can_auto_retry(row):
                continue
        elif row.status != STATUS_PENDING:
            if wanted is not None:
                skipped.append((row.id, "Only pending submissions can be approved."))
            continue
        try:
            await enqueue_approve(
                db,
                row.id,
                retry=retry,
                auto_retry=auto_retry,
                authorized_by=authorized_by,
                new_api_priority=new_api_priority,
                new_api_weight=new_api_weight,
            )
            started.append(row.id)
        except SubmitError as exc:
            skipped.append((row.id, str(exc)))
    return started, skipped


async def kick_auto_approve_queue(
    db: AsyncSession, group: str | None = None
) -> tuple[list[int], list[tuple[int, str]]]:
    """Drain the backlog for one group, or for every group whose toggle is on."""
    from app.services.join_enrollee_service import auto_approve_settings

    if group is None:
        enabled = [name for name, on in (await auto_approve_settings(db)).items() if on]
    else:
        enabled = [normalize_group(group)]
    started: list[int] = []
    skipped: list[tuple[int, str]] = []
    for name in enabled:
        for retry in (False, True):
            batch, batch_skipped = await enqueue_approve_many(
                db, None, retry=retry, auto_retry=retry, authorized_by=AUTH_AUTO, group=name
            )
            started += batch
            skipped += batch_skipped
    return started, skipped


async def _auto_retry_approve(request_id: int) -> None:
    await asyncio.sleep(AUTO_RETRY_DELAY_SECONDS + random.uniform(0, 8))
    if approve_aborted(request_id) or _approve_has_work(request_id):
        return
    from app.database import SessionLocal
    from app.services.join_enrollee_service import is_auto_approve_enabled

    async with SessionLocal() as db:
        row = await get_request_by_id(db, request_id)
        if row is None or not await is_auto_approve_enabled(db, row.group_tag):
            return
        if not _row_can_auto_retry(row):
            return
        try:
            await enqueue_approve(db, request_id, retry=True, auto_retry=True, authorized_by=AUTH_AUTO)
        except SubmitError as exc:
            logger.info("Auto-retry skipped request=%s: %s", request_id, exc)


def schedule_auto_retry(request_id: int) -> None:
    if approve_aborted(request_id) or _approve_has_work(request_id) or _retry_has_work(request_id):
        return
    register_retry_task(request_id, asyncio.create_task(_auto_retry_approve(request_id)))


async def _maybe_auto_retry_after_job(request_id: int) -> None:
    if approve_aborted(request_id):
        return
    from app.database import SessionLocal
    from app.services.join_enrollee_service import is_auto_approve_enabled

    async with SessionLocal() as db:
        row = await get_request_by_id(db, request_id)
        if row is None or not await is_auto_approve_enabled(db, row.group_tag):
            return
        if not _row_can_auto_retry(row):
            return
    schedule_auto_retry(request_id)


async def _run_approve_job(
    request_id: int,
    new_api_priority: int,
    new_api_weight: int,
    authorized_by: str,
) -> None:
    from app.database import SessionLocal
    from app.services.deploy_job_runner import deploy_slots

    try:
        async with deploy_slots(1):
            async with SessionLocal() as db:
                try:
                    await execute_approve(db, request_id, new_api_priority, new_api_weight, authorized_by)
                except asyncio.CancelledError:
                    current = await get_request_by_id(db, request_id)
                    if current is not None and current.status == STATUS_APPROVING:
                        await apply_submit_failure(
                            db,
                            current,
                            "Deploy was cancelled.",
                            keep_network=True,
                            error_kind=ERROR_KIND_DEPLOY,
                        )
                    raise
                except Exception:
                    logger.exception("Approve job failed request=%s", request_id)
                    current = await get_request_by_id(db, request_id)
                    if current is not None and current.status == STATUS_APPROVING:
                        await apply_submit_failure(
                            db,
                            current,
                            "Deploy failed on the server.",
                            keep_network=True,
                            error_kind=ERROR_KIND_DEPLOY,
                        )
    finally:
        asyncio.create_task(_maybe_auto_retry_after_job(request_id))


def _newapi_configured() -> bool:
    try:
        from app.services.kimi_newapi import kimi_pool_gateway

        kimi_pool_gateway()
    except Exception:
        return False
    return True


async def _ensure_approve_newapi(
    db: AsyncSession,
    payload: dict[str, str],
    results: list[Any],
    new_api_priority: int,
    new_api_weight: int,
) -> list[Any]:
    """Auto-approve used to mark Azure success as done even when the O1 channel was missing."""
    if not results:
        return results
    result = results[0]
    if not result.ok or result.new_api_present or not _newapi_configured():
        return results
    from app.services.kimi_deploy_service import add_kimi_newapi_channels

    retry_payload = dict(payload)
    if result.account_name:
        retry_payload["account_name"] = result.account_name
    if result.azure_openai_endpoint:
        retry_payload["azure_openai_endpoint"] = result.azure_openai_endpoint
    if result.resource_group:
        retry_payload["resource_group"] = result.resource_group
    if result.deployment_name:
        retry_payload["deployment_name"] = result.deployment_name
    try:
        retried = await add_kimi_newapi_channels(
            [retry_payload],
            db,
            priority=new_api_priority,
            weight=new_api_weight,
        )
    except Exception:
        logger.exception("NewAPI retry failed after approve deploy")
        retried = []
    if retried:
        retried[0].ok = result.ok
        retried[0].error = result.error
        if not retried[0].account_name:
            retried[0].account_name = result.account_name
        if not retried[0].azure_openai_endpoint:
            retried[0].azure_openai_endpoint = result.azure_openai_endpoint
        results = retried
        result = results[0]
    if result.new_api_present:
        return results
    detail = (result.new_api_error or "Could not add NewAPI channel.")[:800]
    result.ok = False
    result.error = detail
    return results


async def execute_approve(
    db: AsyncSession,
    request_id: int,
    new_api_priority: int,
    new_api_weight: int,
    authorized_by: str,
) -> list[Any]:
    from app.services.kimi_deploy_service import KimiDeployError, deploy_accounts

    row = await get_request_by_id(db, request_id)
    if row is None or row.status != STATUS_APPROVING:
        return []
    if not row.client_secret_encrypted or not row.client_id or not row.subscription_id or not row.tenant_id:
        await apply_submit_failure(
            db,
            row,
            "This request is missing a stored identity. Ask the user to submit again.",
            keep_network=True,
            error_kind=ERROR_KIND_DEPLOY,
        )
        return []

    secret = get_secret_box().decrypt(row.client_secret_encrypted)
    payload = deploy_payload_from_row(row, secret)

    try:
        results = await deploy_accounts(
            [payload],
            1,
            session=db,
            new_api_priority=new_api_priority,
            new_api_weight=new_api_weight,
            persist_principals=False,
        )
    except KimiDeployError as exc:
        await db.refresh(row)
        if approve_aborted(request_id) or row.status != STATUS_APPROVING:
            raise SubmitError("This submission is no longer being approved.") from exc
        row.status = STATUS_FAILED
        row.error_kind = ERROR_KIND_DEPLOY
        row.error_message = scrub_az_text(str(exc))[:800]
        await db.commit()
        return []

    await db.refresh(row)
    if approve_aborted(request_id) or row.status != STATUS_APPROVING:
        return results
    ok = bool(results and results[0].ok)
    if ok:
        results = await _ensure_approve_newapi(db, payload, results, new_api_priority, new_api_weight)
        ok = bool(results and results[0].ok)
    notice = None
    if ok:
        await persist_service_principals(db, [payload], elevated_access=True)
        await db.refresh(row)
        if approve_aborted(request_id) or row.status != STATUS_APPROVING:
            return results
        row.status = STATUS_APPROVED
        row.approved_at = _utcnow()
        row.error_message = None
        row.error_kind = None
        _wipe_secret(row)
        result = results[0]
        notice = (
            (row.person_associated or "").strip() or (result.owner_tag or "").strip() or "Unknown",
            (result.name or row.name or "").strip() or "account",
            authorized_by,
            normalize_group(row.group_tag),
        )
    else:
        row.status = STATUS_FAILED
        row.approved_at = None
        detail = (results[0].error if results else None) or "Deploy failed."
        row.error_kind = ERROR_KIND_DEPLOY
        mod = load_deploy_module()
        humanize = getattr(mod, "humanize_kimi_deploy_error", None)
        if callable(humanize):
            detail = humanize(detail)
        row.error_message = scrub_az_text(detail)[:800]
    await db.commit()
    if notice:
        from app.services.telegram_service import schedule_k3_deployed_notice

        schedule_k3_deployed_notice(*notice)
    return results


async def cancel_session(db: AsyncSession, session_id: str) -> None:
    row = await get_request(db, session_id)
    if row is None:
        raise SubmitError("Unknown submit session.")
    if row.status not in {STATUS_LOGIN_STARTED, STATUS_LOGGED_IN, STATUS_CREATING_SP}:
        raise SubmitError("This session cannot be cancelled.")
    sid = row.session_id
    await abort_session_work(sid)
    await db.refresh(row)
    if row.status in {STATUS_LOGIN_STARTED, STATUS_LOGGED_IN, STATUS_CREATING_SP}:
        await apply_submit_failure(db, row, "Cancelled.")
    await _publish(sid, {"type": "error", "detail": "Cancelled."})
