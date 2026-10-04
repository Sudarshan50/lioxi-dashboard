"""GPT models that can be deployed on 10k accounts, and the quota math behind them.

Azure catalog rows and usage meters are parsed here so deploy planning can be
tested without calling Azure. Capacity units follow the catalog rateLimits:
token count per unit is TPM, request count per unit is RPM.
"""

from __future__ import annotations

import json
import re
from typing import Any

GPT_STACK_ID = "gpt-stack"
GPT_MODEL_NAMES: tuple[str, ...] = (
    "gpt-5.5",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-6-astra",
    "gpt-6-sol",
    "gpt-6-luna",
)
# Higher TPM first. GlobalStandard is 1000 units on these subscriptions;
# DataZoneStandard is 333. The planner still picks whichever SKU yields more TPM.
PREFERRED_SKUS: tuple[str, ...] = ("GlobalStandard", "DataZoneStandard")

GPT_CHANNEL_PREFIX = "gpt-astra-proxy"
GPT_CHANNEL_TAG = "gpt-astra"
GPT_CHANNEL_GROUP = "gpt-stack"
# Selectable O1 group. Ratio 1 matches the default multiplier.
GPT_GROUP_PATCHES: dict[str, dict] = {
    "GroupRatio": {GPT_CHANNEL_GROUP: 1},
    "UserUsableGroups": {GPT_CHANNEL_GROUP: "gpt-stack"},
}
GPT_AZURE_API_VERSION = "2025-04-01-preview"
GPT_DEPLOY_API_VERSION = "2024-10-01"
GPT_MODELS_API_VERSION = "2024-10-01"
GPT_USAGES_API_VERSION = "2023-05-01"
GPT_CHANNEL_TYPE = 3
# Azure rejects max_tokens on these models. Move it only when the caller sent it.
GPT_PARAM_OVERRIDE = json.dumps(
    {
        "operations": [
            {
                "mode": "move",
                "from": "max_tokens",
                "to": "max_completion_tokens",
                "conditions": [{"path": "max_tokens", "mode": "gte", "value": 0}],
            }
        ]
    },
    separators=(",", ":"),
)
# Global Standard short context, same p/c/cr/cc formula already used for gpt-6-luna.
# $2 input, $10 output, $0.20 cached input, $2.50 cache write, per 1M tokens.
GPT_SOL_BILLING_EXPR = 'tier("base", p * 2 + c * 10 + cr * 0.2 + cc * 2.5)'
GPT_PRICE_PATCHES: dict[str, dict] = {
    "ModelRatio": {"gpt-6-sol": 1},
    "CompletionRatio": {"gpt-6-sol": 5},
    "CacheRatio": {"gpt-6-sol": 0.1},
    "CreateCacheRatio": {"gpt-6-sol": 1.25},
    "CompletionRatioMeta": {"gpt-6-sol": {"ratio": 5, "locked": False}},
    "billing_setting.billing_expr": {"gpt-6-sol": GPT_SOL_BILLING_EXPR},
    "billing_setting.billing_mode": {"gpt-6-sol": "tiered_expr"},
}

GPT_CHANNEL_RE = re.compile(rf"^{re.escape(GPT_CHANNEL_PREFIX)}(\d+)$", re.I)


def is_gpt_model(name: str | None) -> bool:
    return (name or "").strip() in GPT_MODEL_NAMES


def resolve_requested_models(stack: bool, models: list[str] | None) -> list[str]:
    if stack:
        return list(GPT_MODEL_NAMES)
    wanted: list[str] = []
    for name in models or []:
        cleaned = (name or "").strip()
        if not cleaned:
            continue
        if cleaned not in GPT_MODEL_NAMES:
            raise ValueError(f"Unknown model {cleaned}.")
        if cleaned not in wanted:
            wanted.append(cleaned)
    if not wanted:
        raise ValueError("Choose at least one model, or deploy the GPT stack.")
    return wanted


def _model_block(item: dict) -> dict | None:
    model = item.get("model")
    if isinstance(model, dict) and model.get("name"):
        return model
    return None


def select_catalog_model(items: list[dict], name: str) -> dict | None:
    """Latest default OpenAI version of `name` from an ARM model catalog page."""
    matches: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        model = _model_block(item)
        if model is None:
            continue
        if str(model.get("name") or "") != name:
            continue
        fmt = str(model.get("format") or "OpenAI")
        if fmt.lower() != "openai":
            continue
        matches.append(model)
    if not matches:
        return None
    defaults = [model for model in matches if model.get("isDefaultVersion")]
    pool = defaults or matches
    return max(pool, key=lambda model: str(model.get("version") or ""))


def index_usages(usages: Any) -> dict[str, dict]:
    items = usages.get("value") if isinstance(usages, dict) else usages
    if not isinstance(items, list):
        return {}
    indexed: dict[str, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_name = item.get("name")
        value = raw_name.get("value") if isinstance(raw_name, dict) else raw_name
        if value:
            indexed[str(value)] = item
    return indexed


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def per_unit_rates(sku: dict) -> tuple[int, int]:
    """TPM and RPM granted by one capacity unit. Catalog default is 1000 TPM / 1 RPM."""
    tpm = 1000
    rpm = 1
    for row in sku.get("rateLimits") or []:
        if not isinstance(row, dict):
            continue
        count = _as_int(row.get("count"), 0)
        if count <= 0:
            continue
        key = str(row.get("key") or "")
        if key == "token":
            tpm = count
        elif key == "request":
            rpm = count
    return tpm, rpm


def plan_model(
    model: dict | None,
    usages: Any,
    existing: dict | None = None,
) -> dict[str, Any]:
    """Pick the SKU whose remaining quota produces the most TPM.

    `existing` is the current deployment of this model on the account, if any.
    Its capacity is already inside the usage meter's currentValue, so it is
    added back before choosing the new target.
    """
    name = str((model or {}).get("name") or (existing or {}).get("model") or "")
    base: dict[str, Any] = {
        "name": name,
        "version": str((model or {}).get("version") or "") or None,
        "available": False,
        "sku": None,
        "capacity": None,
        "tpm": None,
        "rpm": None,
        "quota_limit": None,
        "quota_used": None,
        "deployed": existing is not None,
        "deployed_capacity": (existing or {}).get("capacity"),
        "deployed_sku": (existing or {}).get("sku"),
        "reason": None,
    }
    if model is None:
        base["reason"] = "Not in the Azure model catalog for this region."
        return base

    usage_map = index_usages(usages)
    best: dict[str, Any] | None = None
    seen_quota = False
    for sku in model.get("skus") or []:
        if not isinstance(sku, dict):
            continue
        sku_name = str(sku.get("name") or "")
        if sku_name not in PREFERRED_SKUS:
            continue
        usage_name = str(sku.get("usageName") or "")
        usage = usage_map.get(usage_name)
        if usage is None:
            continue
        seen_quota = True
        limit = _as_int(usage.get("limit"))
        current = _as_int(usage.get("currentValue"))
        existing_cap = 0
        if existing and str(existing.get("sku") or "") == sku_name:
            existing_cap = _as_int(existing.get("capacity"))
        available = max(limit - max(current - existing_cap, 0), 0)
        maximum = _as_int((sku.get("capacity") or {}).get("maximum"), available)
        target = min(available, maximum) if maximum > 0 else available
        tpm_unit, rpm_unit = per_unit_rates(sku)
        tpm = target * tpm_unit
        candidate = {
            "sku": sku_name,
            "capacity": target,
            "tpm": tpm,
            "rpm": target * rpm_unit,
            "quota_limit": limit,
            "quota_used": current,
            "usage_name": usage_name,
        }
        if target < 1:
            continue
        if best is None or tpm > int(best["tpm"]) or (
            tpm == int(best["tpm"]) and PREFERRED_SKUS.index(sku_name) < PREFERRED_SKUS.index(str(best["sku"]))
        ):
            best = candidate

    if best is None:
        base["reason"] = (
            "Quota for this model is fully used."
            if seen_quota
            else "No GlobalStandard or DataZoneStandard quota for this model."
        )
        return base
    base.update(best)
    base["available"] = True
    base["reason"] = None
    return base


def deployment_name(model_name: str) -> str:
    return model_name.strip()


def index_existing_deployments(deployments: list[dict]) -> dict[str, dict]:
    """Map model name → current deployment. Deployment name is the model name."""
    found: dict[str, dict] = {}
    for item in deployments:
        if not isinstance(item, dict):
            continue
        props = item.get("properties") or {}
        model = props.get("model") or {}
        model_name = str(model.get("name") or item.get("name") or "")
        if model_name not in GPT_MODEL_NAMES and str(item.get("name") or "") not in GPT_MODEL_NAMES:
            continue
        key = model_name if model_name in GPT_MODEL_NAMES else str(item.get("name"))
        sku = item.get("sku") or {}
        found[key] = {
            "deployment_name": item.get("name") or key,
            "model": key,
            "version": model.get("version"),
            "sku": sku.get("name"),
            "capacity": _as_int(sku.get("capacity")),
            "state": props.get("provisioningState"),
        }
    return found


def next_gpt_channel_name(channels: list[dict]) -> str:
    highest = -1
    for channel in channels:
        match = GPT_CHANNEL_RE.match(str(channel.get("name") or "").strip())
        if match:
            highest = max(highest, int(match.group(1)))
    return f"{GPT_CHANNEL_PREFIX}{highest + 1}"


def merge_price_maps(current: dict, patch: dict) -> dict:
    """Add missing model prices. Leave an existing price alone."""
    merged = dict(current)
    for key, value in patch.items():
        if key not in merged:
            merged[key] = value
    return merged


def merge_model_list(existing: str | None, names: list[str]) -> str:
    seen: list[str] = []
    for part in str(existing or "").split(",") + list(names):
        item = part.strip()
        if item and item not in seen:
            seen.append(item)
    return ",".join(seen)


def rates_from_deployment(body: dict, fallback_capacity: int) -> tuple[int, int, int]:
    """Read the TPM/RPM Azure actually applied, falling back to capacity × catalog rates."""
    sku = body.get("sku") or {}
    capacity = _as_int(sku.get("capacity"), fallback_capacity)
    props = body.get("properties") or {}
    limits = {str(row.get("key")): row for row in (props.get("rateLimits") or []) if isinstance(row, dict)}
    token = limits.get("token") or {}
    request = limits.get("request") or {}
    tpm = _as_int(token.get("count"), capacity * 1000)
    rpm = _as_int(request.get("count"), capacity)
    return capacity, tpm, rpm
