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
from typing import Any, Awaitable, Callable

import httpx
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.crypto import get_secret_box
from app.models.azure_service_principal import AzureServicePrincipal
from app.models.provider_account import ProviderAccount
from app.models.sp_submit_request import SpSubmitRequest
from app.schemas.submit import PendingRequestPublic, SubmitSessionSnapshot, SubmitSubscription
from app.services.account_service import allocate_unique_name
from app.services.az_cli_session import (
    AzCliError,
    drop_az_session,
    get_az_session,
    normalize_tenant_id,
    scrub_az_text,
)
from app.services.kimi_deploy_service import load_deploy_module
from app.services.owner_tag import parse_owner_tag
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

OPEN_LOGIN = {STATUS_LOGIN_STARTED, STATUS_LOGGED_IN}
TERMINAL = {STATUS_APPROVED, STATUS_REJECTED, STATUS_FAILED, STATUS_EXPIRED}
LIVE_SUB_STATUSES = (STATUS_PENDING, STATUS_CREATING_SP, STATUS_APPROVING)
DUPLICATE_SUB_MESSAGE = (
    "This Azure subscription is already submitted. An admin must decline it before you can register again."
)
ALREADY_ONBOARDED_MESSAGE = "This Azure subscription is already in the portal."

SP_NAME = "usage-and-credits-monitor"
CREATING_SP_MAX_AGE = timedelta(minutes=25)
APPROVING_MAX_AGE = timedelta(minutes=45)
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
_aborted_sessions: set[str] = set()
_aborted_approvals: set[int] = set()


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
        if state.lower() not in {"enabled", ""}:
            continue
        sid = str(item.get("id") or "").strip()
        if not sid:
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


def _session_has_work(session_id: str | None) -> bool:
    sid = _sid(session_id)
    login = _login_tasks.get(sid)
    commit = _commit_tasks.get(sid)
    return (login is not None and not login.done()) or (commit is not None and not commit.done())


def _approve_has_work(request_id: int) -> bool:
    task = _approve_tasks.get(request_id)
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
        await drop_az_session(old.session_id)
        await db.delete(old)


async def list_owner_names(db: AsyncSession) -> list[str]:
    names: set[str] = set()
    for stmt in (
        select(ProviderAccount.owner_tag),
        select(AzureServicePrincipal.owner_tag),
        select(SpSubmitRequest.person_associated),
    ):
        rows = await db.execute(stmt)
        for (tag,) in rows.all():
            value = (tag or "").strip()
            if value:
                names.add(value)
    return sorted(names, key=lambda item: item.lower())


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


async def expire_stale(db: AsyncSession) -> int:
    now = _utcnow()
    login_cutoff = now - timedelta(seconds=_ttl_seconds())
    rows = (
        await db.execute(
            select(SpSubmitRequest).where(
                or_(
                    and_(
                        SpSubmitRequest.status.in_(tuple(OPEN_LOGIN)),
                        SpSubmitRequest.updated_at < login_cutoff,
                    ),
                    SpSubmitRequest.status.in_((STATUS_CREATING_SP, STATUS_APPROVING)),
                )
            )
        )
    ).scalars()
    count = 0
    for row in rows:
        if row.status == STATUS_CREATING_SP:
            if _session_has_work(row.session_id):
                continue
            age = (now - (row.updated_at or now)) if row.updated_at else CREATING_SP_MAX_AGE
            if age < CREATING_SP_MAX_AGE:
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
            age = (now - (row.updated_at or now)) if row.updated_at else APPROVING_MAX_AGE
            if age < APPROVING_MAX_AGE:
                continue
            await abort_approve_work(row.id)
            await apply_submit_failure(
                db,
                row,
                "Interrupted. The server restarted or deploy timed out. Use Retry deploy.",
                keep_network=True,
                error_kind=ERROR_KIND_DEPLOY,
            )
            count += 1
            continue
        _wipe_secret(row)
        row.status = STATUS_EXPIRED
        row.error_message = row.error_message or "Session expired."
        await abort_session_work(row.session_id)
        row.az_config_dir = None
        count += 1
        await _publish(row.session_id, {"type": "error", "detail": "Session expired. Start again."})
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
        async with SessionLocal() as db:
            row = await get_request(db, session_id)
            if row is not None:
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


async def commit_session(
    db: AsyncSession,
    session_id: str,
    subscription_id: str,
    person_associated: str,
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

    try:
        person = parse_owner_tag(person_associated)
    except ValueError as exc:
        raise SubmitError(str(exc)) from exc
    if not person:
        raise SubmitError("Enter a name tag.")

    wanted = subscription_id.strip().lower()
    snap = public_snapshot(row)
    match = next((item for item in snap.subscriptions if item.subscription_id.lower() == wanted), None)
    if match is None:
        raise SubmitError("That subscription is not on this Azure login.")

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

    mod = load_deploy_module()
    slug = mod.slugify(person)
    label = "".join(person.split())
    taken = {
        str(name).lower()
        for (name,) in (await db.execute(select(ProviderAccount.name))).all()
        if name
    }
    taken.update(
        str(name).lower()
        for (name,) in (
            await db.execute(select(SpSubmitRequest.name).where(SpSubmitRequest.id != row.id))
        ).all()
        if name
    )
    display_name = allocate_unique_name(f"Lioxi-{label or slug}", taken)
    row.status = STATUS_CREATING_SP
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
        raise SubmitError(DUPLICATE_SUB_MESSAGE) from exc

    async def emit(event: dict[str, Any]) -> None:
        payload = {**event, "session_id": session_id}
        await _publish(session_id, payload)
        if on_progress is not None:
            await on_progress(payload)

    await emit({"type": "phase", "phase": "sp", "message": "Creating monitor identity…"})
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

    oid = await az.sp_object_id(app_id)
    if stored is None and oid:
        await az.add_sp_as_app_owner(app_id, oid)
    await emit({"type": "phase", "phase": "roles", "message": "Assigning Azure roles…"})
    mod = load_deploy_module()
    roles: list[str] = list(getattr(mod, "ALL_ROLES", []))
    admin_roles = set(getattr(mod, "ADMIN_ROLES", _DEFAULT_ADMIN_ROLES))
    if "Contributor" in roles:
        roles = ["Contributor"] + [item for item in roles if item != "Contributor"]
    failed_by_role: dict[str, str] = {}
    for role in roles:
        if session_aborted(row.session_id):
            raise asyncio.CancelledError
        await emit({"type": "phase", "phase": "roles", "message": f"Assigning {role}…"})
        ok, err = await az.assign_role(app_id, role, sub, object_id=oid, timeout=90 if role == "Contributor" else 45)
        if not ok:
            failed_by_role[role] = err or "assignment failed"

    admin_failed = [f"{role}: {err}" for role, err in failed_by_role.items() if role in admin_roles]
    if admin_failed:
        raise SubmitError(
            "Could not assign required admin roles on this subscription. " + "; ".join(admin_failed)[:800]
        )

    billing_err = None
    tenant = str(row.tenant_id or "")
    if oid and tenant:
        await emit({"type": "phase", "phase": "billing", "message": "Assigning billing reader…"})
        ok, err = await az.assign_billing_reader(oid, tenant)
        if not ok:
            billing_err = err

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
    await abort_session_work(session_id)
    leftover_error: str | None = None
    if row.client_secret_encrypted and row.client_id and row.tenant_id and row.subscription_id:
        try:
            from app.services.kimi_deploy_service import delete_accounts

            secret = get_secret_box().decrypt(row.client_secret_encrypted)
            results = await delete_accounts([deploy_payload_from_row(row, secret)], jobs=1)
            if results and not results[0].ok:
                leftover_error = results[0].error or "Azure leftover delete did not complete."
        except Exception as exc:
            leftover_error = str(exc)[:400]
            logger.exception("Could not remove leftover Kimi stack for submit %s", deleted_id)
        if leftover_error and (row.error_kind == ERROR_KIND_DEPLOY or row.status == STATUS_FAILED):
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


async def approve_request(
    db: AsyncSession,
    request_id: int,
    jobs: int,
    new_api_priority: int,
    new_api_weight: int,
    on_progress: ProgressFn | None = None,
) -> list[Any]:
    from app.services.kimi_deploy_service import KimiDeployError, deploy_accounts

    row = (
        await db.execute(select(SpSubmitRequest).where(SpSubmitRequest.id == request_id).with_for_update())
    ).scalar_one_or_none()
    if row is None:
        raise SubmitError("Unknown pending request.")
    if row.status == STATUS_APPROVING:
        raise SubmitError("This submission is already being approved.")
    if row.status not in {STATUS_PENDING, STATUS_FAILED}:
        raise SubmitError("Only pending submissions can be approved.")
    if row.status == STATUS_FAILED and row.error_kind != ERROR_KIND_DEPLOY:
        raise SubmitError("This failure cannot be retried with Retry deploy. Ask them to join again.")
    if not row.client_secret_encrypted or not row.client_id or not row.subscription_id or not row.tenant_id:
        raise SubmitError("This request is missing a stored identity. Ask the user to submit again.")

    secret = get_secret_box().decrypt(row.client_secret_encrypted)
    payload = deploy_payload_from_row(row, secret)
    row.status = STATUS_APPROVING
    await db.commit()

    try:
        results = await deploy_accounts(
            [payload],
            jobs,
            session=db,
            new_api_priority=new_api_priority,
            new_api_weight=new_api_weight,
            on_progress=on_progress,
            persist_principals=False,
        )
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
    except KimiDeployError as exc:
        await db.refresh(row)
        if approve_aborted(request_id) or row.status != STATUS_APPROVING:
            raise SubmitError("This submission is no longer being approved.") from exc
        row.status = STATUS_FAILED
        row.error_kind = ERROR_KIND_DEPLOY
        row.error_message = scrub_az_text(str(exc))[:800]
        await db.commit()
        raise SubmitError(str(exc)) from exc

    await db.refresh(row)
    if approve_aborted(request_id) or row.status != STATUS_APPROVING:
        return results
    ok = bool(results and results[0].ok)
    if ok:
        await persist_service_principals(db, [payload])
        await db.refresh(row)
        if approve_aborted(request_id) or row.status != STATUS_APPROVING:
            return results
        row.status = STATUS_APPROVED
        row.approved_at = _utcnow()
        row.error_message = None
        row.error_kind = None
        _wipe_secret(row)
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
