from __future__ import annotations

import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_setting import AppSetting

DEPLOY_DEFAULTS_KEY = "deploy_defaults"
DEFAULT_PRIORITY = 10
DEFAULT_WEIGHT = 1


def normalize_deploy_defaults(payload: dict | None = None) -> dict[str, int]:
    data = payload or {}
    try:
        priority = int(data.get("priority", DEFAULT_PRIORITY))
        weight = int(data.get("weight", DEFAULT_WEIGHT))
    except (TypeError, ValueError) as exc:
        raise ValueError("Priority and weight must be integers") from exc
    if not 0 <= priority <= 10000:
        raise ValueError("Priority must be between 0 and 10000")
    if not 1 <= weight <= 10000:
        raise ValueError("Weight must be between 1 and 10000")
    return {"priority": priority, "weight": weight}


async def get_deploy_defaults(session: AsyncSession) -> dict[str, int]:
    row = await session.get(AppSetting, DEPLOY_DEFAULTS_KEY)
    if row is None:
        return {"priority": DEFAULT_PRIORITY, "weight": DEFAULT_WEIGHT}
    try:
        return normalize_deploy_defaults(json.loads(row.value))
    except (ValueError, TypeError):
        return {"priority": DEFAULT_PRIORITY, "weight": DEFAULT_WEIGHT}


async def save_deploy_defaults(session: AsyncSession, payload: dict) -> dict[str, int]:
    config = normalize_deploy_defaults(payload)
    row = await session.get(AppSetting, DEPLOY_DEFAULTS_KEY)
    if row is None:
        session.add(AppSetting(key=DEPLOY_DEFAULTS_KEY, value=json.dumps(config)))
    else:
        row.value = json.dumps(config)
    await session.commit()
    return config


async def resolve_routing(
    session: AsyncSession,
    priority: int | None,
    weight: int | None,
) -> tuple[int, int]:
    defaults = await get_deploy_defaults(session)
    return (
        defaults["priority"] if priority is None else priority,
        defaults["weight"] if weight is None else weight,
    )
