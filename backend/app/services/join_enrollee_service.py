from __future__ import annotations

import json
import logging

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_setting import AppSetting
from app.models.azure_service_principal import AzureServicePrincipal
from app.models.join_enrollee import JoinEnrollee
from app.models.provider_account import ProviderAccount
from app.models.sp_submit_request import SpSubmitRequest
from app.services.join_group import GROUP_SB, GROUP_VCS, GROUPS, group_label, normalize_group
from app.services.owner_tag import join_picker_name, parse_owner_tag

logger = logging.getLogger(__name__)

# SB keeps the original key so an existing toggle survives the VCS split.
JOIN_AUTO_APPROVE_KEYS = {GROUP_SB: "join_auto_approve", GROUP_VCS: "join_auto_approve_vcs"}
JOIN_ENROLLEES_SEEDED_KEY = "join_enrollees_seeded"
SEED_NAME = "Snig"


class EnrolleeError(ValueError):
    pass


def normalize_enrollee_name(value: str | None) -> str:
    try:
        name = parse_owner_tag(value)
    except ValueError as exc:
        raise EnrolleeError(str(exc)) from exc
    if not name:
        raise EnrolleeError("Enter a name.")
    return name


def normalize_auto_approve(payload: dict | None = None) -> bool:
    # A hand-edited setting can be any JSON scalar; anything but an object
    # means "off" rather than an AttributeError on every Join and Pending call.
    data = payload if isinstance(payload, dict) else {}
    value = data.get("enabled", False)
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "on"}:
        return True
    if isinstance(value, (int, float)) and value == 1:
        return True
    return False


async def is_auto_approve_enabled(session: AsyncSession, group: str | None = GROUP_SB) -> bool:
    """Auto-approve is per group: SB and VCS are switched independently."""
    row = await session.get(AppSetting, JOIN_AUTO_APPROVE_KEYS[normalize_group(group)])
    if row is None:
        return False
    try:
        return normalize_auto_approve(json.loads(row.value))
    except (ValueError, TypeError, json.JSONDecodeError):
        return False


async def auto_approve_settings(session: AsyncSession) -> dict[str, bool]:
    return {group: await is_auto_approve_enabled(session, group) for group in GROUPS}


async def save_auto_approve(session: AsyncSession, enabled: bool, group: str | None = GROUP_SB) -> bool:
    key = JOIN_AUTO_APPROVE_KEYS[normalize_group(group)]
    payload = json.dumps({"enabled": bool(enabled)})
    row = await session.get(AppSetting, key)
    if row is None:
        session.add(AppSetting(key=key, value=payload))
    else:
        row.value = payload
    await session.commit()
    return bool(enabled)


async def discover_legacy_join_names(session: AsyncSession) -> list[str]:
    """Names found on pre-split data. All of it predates VCS, so all of it is SB."""
    names: set[str] = set()
    for stmt in (
        select(ProviderAccount.owner_tag),
        select(AzureServicePrincipal.owner_tag),
        select(SpSubmitRequest.person_associated),
    ):
        rows = await session.execute(stmt)
        for (tag,) in rows.all():
            person = join_picker_name(tag)
            if person:
                names.add(person)
    names.add(SEED_NAME)
    return sorted(names, key=lambda item: item.lower())


async def ensure_enrollees_seeded(session: AsyncSession) -> int:
    flag = await session.get(AppSetting, JOIN_ENROLLEES_SEEDED_KEY)
    if flag is not None:
        return 0
    count = (await session.execute(select(func.count()).select_from(JoinEnrollee))).scalar_one()
    added = 0
    if not count:
        for name in await discover_legacy_join_names(session):
            session.add(JoinEnrollee(name=name, group_tag=GROUP_SB, banned=False))
            added += 1
    session.add(AppSetting(key=JOIN_ENROLLEES_SEEDED_KEY, value="1"))
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return 0
    if added:
        logger.info("Seeded %s join enrollee name(s)", added)
    return added


async def list_enrollees(session: AsyncSession, group: str | None = None) -> list[JoinEnrollee]:
    await ensure_enrollees_seeded(session)
    stmt = select(JoinEnrollee)
    if group is not None:
        stmt = stmt.where(JoinEnrollee.group_tag == normalize_group(group))
    rows = (
        await session.execute(stmt.order_by(JoinEnrollee.group_tag, func.lower(JoinEnrollee.name)))
    ).scalars()
    return list(rows)


async def list_join_picker_names(session: AsyncSession, group: str | None = GROUP_SB) -> list[str]:
    """Unbanned names enrolled in this group. The Join dropdown offers exactly
    these, and a submission is rejected unless the picked name is one of them."""
    await ensure_enrollees_seeded(session)
    rows = await session.execute(
        select(JoinEnrollee.name).where(
            JoinEnrollee.banned.is_(False),
            JoinEnrollee.group_tag == normalize_group(group),
        )
    )
    names: set[str] = set()
    for (name,) in rows.all():
        person = join_picker_name(name)
        if person:
            names.add(person)
    return sorted(names, key=lambda item: item.lower())


async def create_enrollee(session: AsyncSession, raw_name: str, group: str | None = GROUP_SB) -> JoinEnrollee:
    name = normalize_enrollee_name(raw_name)
    tag = normalize_group(group)
    label = group_label(tag)
    existing = (
        await session.execute(
            select(JoinEnrollee).where(
                func.lower(JoinEnrollee.name) == name.lower(),
                JoinEnrollee.group_tag == tag,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise EnrolleeError(f"{name} is already on the {label} list.")
    row = JoinEnrollee(name=name, group_tag=tag, banned=False)
    session.add(row)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise EnrolleeError(f"{name} is already on the {label} list.") from exc
    await session.refresh(row)
    return row


async def set_enrollee_banned(session: AsyncSession, enrollee_id: int, banned: bool) -> JoinEnrollee:
    row = await session.get(JoinEnrollee, enrollee_id)
    if row is None:
        raise EnrolleeError("Unknown name.")
    row.banned = bool(banned)
    await session.commit()
    await session.refresh(row)
    return row
