#!/usr/bin/env python3
"""Bootstrap a combined viewer+admin SP, then deploy FW-Kimi-K3 at max TPM/RPM.

Usage
-----
# On an Owner `az login` (creates the SP, writes secrets JSON):
python3 scripts/kimi_k3_deploy.py bootstrap --name ch7221499 --email ch7221499@gmail.com \\
    --out /path/to/ch7221499.json

# Current Owner `az login` → create/reset SP → append/update new_final.json:
python3 scripts/add_login_sp.py
python3 scripts/kimi_k3_deploy.py add-login --out new_final.json

# From an array of those SP secrets (runs in parallel):
python3 scripts/kimi_k3_deploy.py deploy --input secrets.json --out results.json

secrets.json is either a list of account objects, or {"accounts": [...]}.
Each object needs AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET,
AZURE_SUBSCRIPTION_ID. Optional: name, account_holder.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import string
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

SP_NAME = "usage-and-credits-monitor"
MODEL_NAME = "FW-Kimi-K3"
MODEL_VERSION = "1"
MODEL_FORMAT = "Fireworks"
SKU_NAME = "DataZoneStandard"
LOCATION = "eastus2"
KIND = "AIServices"
ACCOUNT_SKU = "S0"
QUOTA_NAME = "AIServices.DataZoneStandard.Fireworks"
DEPLOYMENT_NAME = "FW-Kimi-K3"
API_VERSION = "2023-05-01"
DEPLOY_API_VERSION = "2024-10-01"
FIREWORKS_FEATURE = "Fireworks.EnableDeploy"
DEFAULT_FIREWORKS_CAP = 500

VIEWER_ROLES = [
    "Reader",
    "Monitoring Reader",
    "Cost Management Reader",
    "Billing Reader",
    "Cognitive Services Usages Reader",
]
ADMIN_ROLES = [
    "Contributor",
    "Cognitive Services Contributor",
    "Foundry Owner",
    "Foundry User",
    "Azure AI Developer",
]
ALL_ROLES = VIEWER_ROLES + ADMIN_ROLES
PROVIDERS = [
    "Microsoft.CognitiveServices",
    "Microsoft.Insights",
]
BILLING_ACCOUNT_READER = "50000000-aaaa-bbbb-cccc-100000000002"


class AzError(RuntimeError):
    pass


def _run(cmd: list[str], env: dict[str, str] | None = None, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env or os.environ.copy(),
        timeout=timeout,
    )


def az_json(cmd: list[str], env: dict[str, str] | None = None, timeout: int = 180) -> Any:
    r = _run(cmd, env=env, timeout=timeout)
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        raise AzError(f"{' '.join(cmd[:6])} … failed ({r.returncode}): {err[-1200:]}")
    text = (r.stdout or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise AzError(f"JSON parse failed for {' '.join(cmd[:6])}: {exc}: {text[:400]}") from exc


def az_ok(cmd: list[str], env: dict[str, str] | None = None, timeout: int = 180) -> tuple[bool, str]:
    r = _run(cmd, env=env, timeout=timeout)
    if r.returncode == 0:
        return True, (r.stdout or "").strip()
    return False, ((r.stderr or r.stdout or "").strip())[-1200:]


def isolated_env(config_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["AZURE_CONFIG_DIR"] = str(config_dir)
    env["AZURE_CORE_COLLECT_TELEMETRY"] = "0"
    env.pop("AZURE_ACCESS_TOKEN", None)
    return env


def slugify(value: str, fallback: str = "acct") -> str:
    raw = (value or fallback).split("@")[0].lower()
    out = "".join(ch if ch.isalnum() else "" for ch in raw)
    return (out or fallback)[:20]


def rand_suffix(n: int = 6) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def wait_for(predicate, timeout: int, interval: float = 5.0, desc: str = "condition") -> None:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        ok, last = predicate()
        if ok:
            return
        time.sleep(interval)
    raise AzError(f"Timed out waiting for {desc}: {last}")


def retry(fn, attempts: int = 8, delay: float = 8.0, desc: str = "operation"):
    last = None
    for i in range(attempts):
        try:
            return fn()
        except AzError as exc:
            last = exc
            msg = str(exc).lower()
            retryable = any(
                s in msg
                for s in (
                    "authorizationfailed",
                    "does not have authorization",
                    "forbidden",
                    "429",
                    "conflict",
                    "notready",
                    "not found",
                    "temporarily",
                    "retry",
                    "timeout",
                )
            )
            if not retryable or i == attempts - 1:
                raise
            time.sleep(delay * (1.2 ** i))
    raise last  # pragma: no cover


# ---------------------------------------------------------------------------
# Pay-as-you-go subscription
# ---------------------------------------------------------------------------

BLOCKED_QUOTA_IDS = (
    "FreeTrial_2014-09-01",
    "AzureForStudents_2018-01-01",
    "AzureForStudentsStarter_2018-01-01",
    "DreamSpark_2015-02-01",
)
PAYG_QUOTA_IDS = (
    "PayAsYouGo_2014-09-01",
    "PayAsYouGo_DevTest_2014-09-01",
    "EnterpriseAgreement_2014-09-01",
    "CSP_2015-05-01",
    "AzurePlan",
    "MPN_2014-09-01",
)


def subscription_policies(env: dict[str, str] | None, sub: str) -> dict[str, Any]:
    data = az_json(
        [
            "az",
            "rest",
            "--method",
            "get",
            "--url",
            f"https://management.azure.com/subscriptions/{sub}?api-version=2022-12-01",
            "-o",
            "json",
        ],
        env=env,
    ) or {}
    return data.get("subscriptionPolicies") or {}


def is_payg_capable(policies: dict[str, Any], state: str = "Enabled") -> bool:
    quota = str(policies.get("quotaId") or "")
    spend = str(policies.get("spendingLimit") or "")
    if state and state.lower() not in ("enabled",):
        return False
    if spend.lower() == "on":
        return False
    if any(quota.startswith(b.split("_")[0]) or quota == b for b in BLOCKED_QUOTA_IDS):
        return False
    if "FreeTrial" in quota or "AzureForStudents" in quota or "DreamSpark" in quota:
        return False
    return True


def _payg_billing_scope(env: dict[str, str] | None) -> str | None:
    """Invoice section under a non-credits MCA profile, if one exists."""
    try:
        bas = az_json(["az", "billing", "account", "list", "-o", "json"], env=env) or []
    except AzError:
        return None
    if isinstance(bas, dict):
        bas = bas.get("value") or []
    if not bas:
        return None
    ba = bas[0].get("name")
    url = (
        f"https://management.azure.com/providers/Microsoft.Billing/billingAccounts/{ba}"
        f"/billingProfiles?api-version=2024-04-01"
    )
    try:
        bp = az_json(["az", "rest", "--method", "get", "--url", url, "-o", "json"], env=env) or {}
    except AzError:
        return None
    profiles = bp.get("value") if isinstance(bp, dict) else bp
    preferred = None
    fallback = None
    for p in profiles or []:
        props = p.get("properties") or {}
        display = str(props.get("displayName") or "")
        name = p.get("name")
        if props.get("status") and str(props.get("status")).lower() != "active":
            continue
        sec_url = (
            f"https://management.azure.com/providers/Microsoft.Billing/billingAccounts/{ba}"
            f"/billingProfiles/{name}/invoiceSections?api-version=2024-04-01"
        )
        try:
            secs = az_json(["az", "rest", "--method", "get", "--url", sec_url, "-o", "json"], env=env) or {}
        except AzError:
            continue
        items = secs.get("value") if isinstance(secs, dict) else secs
        if not items:
            continue
        section = items[0].get("name")
        scope = (
            f"/providers/Microsoft.Billing/billingAccounts/{ba}"
            f"/billingProfiles/{name}/invoiceSections/{section}"
        )
        if "credit" in display.lower():
            fallback = fallback or scope
        else:
            preferred = scope
            break
    return preferred or fallback


def _create_payg_subscription(env: dict[str, str] | None, display_name: str) -> str:
    scope = _payg_billing_scope(env)
    if not scope:
        raise AzError("No MCA billing profile/invoice section found to create a Pay-As-You-Go subscription")
    alias = f"payg-{rand_suffix(8)}"
    url = f"https://management.azure.com/providers/Microsoft.Subscription/aliases/{alias}?api-version=2021-10-01"
    body = json.dumps(
        {
            "properties": {
                "displayName": display_name,
                "workload": "Production",
                "billingScope": scope,
            }
        }
    )
    created = az_json(
        ["az", "rest", "--method", "put", "--url", url, "--body", body, "-o", "json"],
        env=env,
        timeout=180,
    ) or {}

    def _ready() -> tuple[bool, str]:
        try:
            shown = az_json(["az", "rest", "--method", "get", "--url", url, "-o", "json"], env=env) or {}
        except AzError as exc:
            return False, str(exc)[-200:]
        props = shown.get("properties") or {}
        state = str(props.get("provisioningState") or "")
        sub_id = props.get("subscriptionId")
        return bool(sub_id) and state.lower() == "succeeded", f"{state} {sub_id}"

    wait_for(_ready, timeout=180, interval=8, desc="PAYG subscription alias Succeeded")
    shown = az_json(["az", "rest", "--method", "get", "--url", url, "-o", "json"], env=env) or {}
    sub_id = (shown.get("properties") or {}).get("subscriptionId")
    if not sub_id:
        raise AzError(f"PAYG alias created but no subscriptionId: {created}")
    return sub_id


def ensure_payg_subscription(
    env: dict[str, str] | None,
    sub: str,
    *,
    create_if_needed: bool,
    display_name: str = "Pay-As-You-Go",
) -> dict[str, Any]:
    """Return a usage-based sub with spending limit off. Create one under MCA if blocked."""
    acc = az_json(["az", "account", "show", "-o", "json"], env=env) or {}
    policies = subscription_policies(env, sub)
    state = str(acc.get("state") or "Enabled")
    info = {
        "subscription_id": sub,
        "quota_id": policies.get("quotaId"),
        "spending_limit": policies.get("spendingLimit"),
        "state": state,
        "upgraded": False,
        "created": False,
        "action": "already_payg",
    }
    if is_payg_capable(policies, state):
        return info

    # Disabled subs: try enable first.
    if state.lower() != "enabled":
        az_ok(
            ["az", "account", "subscription", "enable", "--id", sub, "-o", "none"],
            env=env,
            timeout=60,
        )
        policies = subscription_policies(env, sub)
        acc = az_json(["az", "account", "show", "-o", "json"], env=env) or {}
        state = str(acc.get("state") or state)
        if is_payg_capable(policies, state):
            info.update(
                {
                    "quota_id": policies.get("quotaId"),
                    "spending_limit": policies.get("spendingLimit"),
                    "state": state,
                    "upgraded": True,
                    "action": "enabled_existing",
                }
            )
            return info

    if not create_if_needed:
        info.update(
            {
                "quota_id": policies.get("quotaId"),
                "spending_limit": policies.get("spendingLimit"),
                "state": state,
                "action": "kept_secrets_subscription",
            }
        )
        return info

    new_sub = _create_payg_subscription(env, display_name)
    az_json(["az", "account", "set", "--subscription", new_sub, "-o", "none"], env=env)
    policies = subscription_policies(env, new_sub)
    info.update(
        {
            "subscription_id": new_sub,
            "quota_id": policies.get("quotaId"),
            "spending_limit": policies.get("spendingLimit"),
            "state": "Enabled",
            "created": True,
            "upgraded": True,
            "action": "created_payg_azure_plan",
            "previous_subscription_id": sub,
        }
    )
    if not is_payg_capable(policies, "Enabled"):
        # New Azure Plan on MCA is still usage-based; accept spendingLimit Off even if quotaId is Sponsored.
        if str(policies.get("spendingLimit") or "").lower() != "on":
            info["action"] = "created_usage_based_plan"
            return info
        raise AzError(
            f"Created subscription {new_sub} but it is still capped "
            f"(quotaId={policies.get('quotaId')}, spendingLimit={policies.get('spendingLimit')})"
        )
    return info


# ---------------------------------------------------------------------------
# Bootstrap (Owner user)
# ---------------------------------------------------------------------------

def bootstrap(name: str, email: str, out_path: Path) -> dict[str, Any]:
    acc = az_json(["az", "account", "show", "-o", "json"])
    user = (acc or {}).get("user") or {}
    if user.get("type") != "user":
        raise AzError(f"Need an Owner user login, got {user.get('type')} {user.get('name')}")
    sub = acc["id"]
    tenant = acc["tenantId"]
    holder = email or user.get("name") or ""

    payg = ensure_payg_subscription(
        None, sub, create_if_needed=False, display_name=f"Lioxi-{name}"
    )
    az_json(["az", "account", "set", "--subscription", sub, "-o", "none"])

    for ns in PROVIDERS:
        az_ok(["az", "provider", "register", "--namespace", ns, "--wait"], timeout=300)
    az_ok(
        [
            "az",
            "feature",
            "register",
            "--namespace",
            "Microsoft.CognitiveServices",
            "--name",
            FIREWORKS_FEATURE,
        ]
    )
    az_ok(["az", "provider", "register", "--namespace", "Microsoft.CognitiveServices"], timeout=120)

    existing = az_json(
        ["az", "ad", "sp", "list", "--display-name", SP_NAME, "--query", "[].{appId:appId,id:id}", "-o", "json"]
    ) or []
    if existing:
        app_id = existing[0]["appId"]
        reset = az_json(
            ["az", "ad", "sp", "credential", "reset", "--id", app_id, "--years", "1", "-o", "json"]
        )
        secret = reset["password"]
        created = {"appId": app_id, "password": secret}
    else:
        created = az_json(
            [
                "az",
                "ad",
                "sp",
                "create-for-rbac",
                "--name",
                SP_NAME,
                "--skip-assignment",
                "--years",
                "1",
                "-o",
                "json",
            ]
        )
        app_id = created["appId"]
        secret = created["password"]
    sp_oid = az_json(["az", "ad", "sp", "show", "--id", app_id, "--query", "id", "-o", "json"])
    if sp_oid:
        az_ok(
            [
                "az",
                "ad",
                "app",
                "owner",
                "add",
                "--id",
                app_id,
                "--owner-object-id",
                str(sp_oid),
            ]
        )

    assigned: list[str] = []
    failed: list[str] = []
    scope = f"/subscriptions/{sub}"
    for role in ALL_ROLES:
        ok, err = az_ok(
            [
                "az",
                "role",
                "assignment",
                "create",
                "--assignee",
                app_id,
                "--role",
                role,
                "--scope",
                scope,
                "-o",
                "none",
            ]
        )
        if ok:
            assigned.append(role)
        elif "already exists" in err.lower() or "exist" in err.lower():
            assigned.append(role)
        else:
            failed.append(f"{role}: {err}")

    billing_ok = False
    billing_err = None
    ba_name = None
    try:
        bas = az_json(["az", "billing", "account", "list", "-o", "json"]) or []
        if isinstance(bas, dict):
            bas = bas.get("value") or []
        if bas:
            ba_name = bas[0].get("name")
            body = json.dumps(
                {
                    "principalId": sp_oid,
                    "principalTenantId": tenant,
                    "roleDefinitionId": (
                        f"/providers/Microsoft.Billing/billingAccounts/{ba_name}"
                        f"/billingRoleDefinitions/{BILLING_ACCOUNT_READER}"
                    ),
                }
            )
            url = (
                f"https://management.azure.com/providers/Microsoft.Billing/billingAccounts/"
                f"{ba_name}/createBillingRoleAssignment?api-version=2024-04-01"
            )
            ok, err = az_ok(["az", "rest", "--method", "post", "--url", url, "--body", body, "-o", "json"])
            billing_ok = ok
            billing_err = None if ok else err
    except AzError as exc:
        billing_err = str(exc)[-400:]

    record = {
        "name": name,
        "account_holder": holder,
        "account_name": f"Lioxi-{name}",
        "service_principal_name": SP_NAME,
        "AZURE_TENANT_ID": tenant,
        "AZURE_CLIENT_ID": app_id,
        "AZURE_CLIENT_SECRET": secret,
        "AZURE_SUBSCRIPTION_ID": sub,
        "subscription_name": acc.get("name"),
        "roles_assigned": assigned,
        "roles_failed": failed,
        "billing_account": ba_name,
        "billing_account_reader": billing_ok,
        "billing_account_reader_error": billing_err,
        "payg": payg,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(record, indent=2) + "\n")
    os.chmod(out_path, 0o600)
    return record


# ---------------------------------------------------------------------------
# Deploy (service principal)
# ---------------------------------------------------------------------------

def fireworks_quota(env: dict[str, str], location: str) -> tuple[int, int]:
    usages = az_json(["az", "cognitiveservices", "usage", "list", "-l", location, "-o", "json"], env=env) or []
    for u in usages:
        name = (u.get("name") or {}).get("value") or ""
        if name == QUOTA_NAME:
            current = int(float(u.get("currentValue") or 0))
            limit = int(float(u.get("limit") or 0))
            return current, limit
    return 0, DEFAULT_FIREWORKS_CAP


def account_ready(env: dict[str, str], name: str, rg: str) -> tuple[bool, str]:
    try:
        data = az_json(
            ["az", "cognitiveservices", "account", "show", "-n", name, "-g", rg, "-o", "json"],
            env=env,
        )
    except AzError as exc:
        return False, str(exc)[-200:]
    state = ((data or {}).get("properties") or {}).get("provisioningState") or ""
    return state.lower() == "succeeded", state


def pick_or_create_account(env: dict[str, str], slug: str, sub: str) -> tuple[str, str, dict[str, Any]]:
    accounts = az_json(["az", "cognitiveservices", "account", "list", "-o", "json"], env=env) or []
    us_accounts = [
        a
        for a in accounts
        if a.get("kind") == KIND and (a.get("location") or "").replace(" ", "").lower() in {
            "eastus2",
            "eastus",
            "westus",
            "westus2",
            "westus3",
            "centralus",
            "northcentralus",
            "southcentralus",
        }
    ]
    east = [a for a in us_accounts if (a.get("location") or "").lower() == LOCATION]
    chosen = (east or us_accounts or [None])[0]
    if chosen:
        rid = chosen["id"]
        rg = rid.split("/")[rid.split("/").index("resourceGroups") + 1]
        return chosen["name"], rg, chosen

    rg = f"rg-{slug}-kimi"
    acct = f"{slug}-kimi-{rand_suffix()}"
    ok, err = az_ok(["az", "group", "create", "-n", rg, "-l", LOCATION, "-o", "none"], env=env)
    if not ok and "already exists" not in err.lower():
        raise AzError(f"resource group create failed: {err}")
    created = retry(
        lambda: az_json(
            [
                "az",
                "cognitiveservices",
                "account",
                "create",
                "-n",
                acct,
                "-g",
                rg,
                "--kind",
                KIND,
                "--sku",
                ACCOUNT_SKU,
                "-l",
                LOCATION,
                "--custom-domain",
                acct,
                "--yes",
                "-o",
                "json",
            ],
            env=env,
            timeout=300,
        ),
        attempts=6,
        delay=10,
        desc="create AIServices account",
    )
    wait_for(lambda: account_ready(env, acct, rg), timeout=240, desc=f"account {acct} succeeded")
    az_ok(
        [
            "az",
            "cognitiveservices",
            "account",
            "project",
            "create",
            "-n",
            acct,
            "-g",
            rg,
            "--project-name",
            f"{slug}-kimi",
            "--location",
            LOCATION,
            "--display-name",
            f"{slug}-kimi",
            "-o",
            "none",
        ],
        env=env,
        timeout=180,
    )
    return acct, rg, created


def put_kimi_deployment(env: dict[str, str], sub: str, rg: str, acct: str, capacity: int) -> dict[str, Any]:
    url = (
        f"https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}"
        f"/providers/Microsoft.CognitiveServices/accounts/{acct}/deployments/{DEPLOYMENT_NAME}"
        f"?api-version={DEPLOY_API_VERSION}"
    )
    body = json.dumps(
        {
            "sku": {"name": SKU_NAME, "capacity": capacity},
            "properties": {
                "model": {
                    "format": MODEL_FORMAT,
                    "name": MODEL_NAME,
                    "version": MODEL_VERSION,
                }
            },
        }
    )
    return az_json(
        ["az", "rest", "--method", "put", "--url", url, "--body", body, "-o", "json"],
        env=env,
        timeout=300,
    )


def ensure_kimi_deployment(env: dict[str, str], sub: str, acct: str, rg: str, location: str) -> dict[str, Any]:
    current, limit = fireworks_quota(env, location)
    if limit <= 0:
        raise AzError(f"Fireworks DataZoneStandard quota is 0 in {location}")

    deployments = (
        az_json(
            ["az", "cognitiveservices", "account", "deployment", "list", "-n", acct, "-g", rg, "-o", "json"],
            env=env,
        )
        or []
    )
    existing = None
    used_by_others = 0
    for d in deployments:
        model = ((d.get("properties") or {}).get("model") or {}).get("name") or ""
        cap = int(((d.get("sku") or {}).get("capacity")) or 0)
        if d.get("name") == DEPLOYMENT_NAME or model == MODEL_NAME:
            existing = d
        else:
            sku = (d.get("sku") or {}).get("name") or ""
            if sku == SKU_NAME:
                used_by_others += cap

    target = max(limit - used_by_others, 1)
    if target > limit:
        target = limit

    current_cap = int(((existing.get("sku") or {}).get("capacity")) or 0) if existing else 0
    if existing is None or current_cap != target:
        shown = retry(
            lambda: put_kimi_deployment(env, sub, rg, acct, target),
            attempts=8,
            delay=15,
            desc="PUT FW-Kimi-K3 deployment",
        )
    else:
        shown = existing

    props = shown.get("properties") or {}
    limits = {r.get("key"): r for r in (props.get("rateLimits") or [])}
    cap = int(((shown.get("sku") or {}).get("capacity")) or target)
    tpm = int((limits.get("token") or {}).get("count") or cap * 1000)
    rpm = int((limits.get("request") or {}).get("count") or cap)
    return {
        "deployment_name": shown.get("name") or DEPLOYMENT_NAME,
        "capacity": cap,
        "tpm": tpm,
        "rpm": rpm,
        "quota_limit": limit,
        "quota_current_before": current,
        "provisioning_state": props.get("provisioningState"),
    }


def resolve_email(env: dict[str, str], fallback: str) -> str:
    if fallback and "@" in fallback and "#EXT#" not in fallback:
        return fallback
    try:
        bas = az_json(["az", "billing", "account", "list", "-o", "json"], env=env) or []
        if isinstance(bas, dict):
            bas = bas.get("value") or []
        if not bas:
            return fallback
        ba = bas[0].get("name")
        url = (
            f"https://management.azure.com/providers/Microsoft.Billing/billingAccounts/{ba}"
            f"/billingProfiles?api-version=2024-04-01"
        )
        bp = az_json(["az", "rest", "--method", "get", "--url", url, "-o", "json"], env=env) or {}
        items = bp.get("value") if isinstance(bp, dict) else bp
        for p in items or []:
            props = p.get("properties") or p
            bill = props.get("billTo") or {}
            email = bill.get("email")
            if email:
                return email
        props = bas[0].get("properties") or {}
        sold = props.get("soldTo") or {}
        if sold.get("email"):
            return sold["email"]
    except AzError:
        pass
    return fallback


def deploy_one(acct: dict[str, Any]) -> dict[str, Any]:
    tenant = acct["AZURE_TENANT_ID"]
    client = acct["AZURE_CLIENT_ID"]
    secret = acct["AZURE_CLIENT_SECRET"]
    sub = acct["AZURE_SUBSCRIPTION_ID"]
    name = acct.get("name") or slugify(acct.get("account_holder") or client)
    fallback_email = acct.get("account_holder") or ""
    slug = slugify(name)

    with tempfile.TemporaryDirectory(prefix=f"az-{slug}-") as tmp:
        env = isolated_env(Path(tmp))
        login = az_json(
            [
                "az",
                "login",
                "--service-principal",
                "-u",
                client,
                "-p",
                secret,
                "--tenant",
                tenant,
                "-o",
                "json",
            ],
            env=env,
            timeout=60,
        )
        if not login:
            raise AzError("SP login returned empty")
        az_json(["az", "account", "set", "--subscription", sub, "-o", "none"], env=env)
        current = az_json(["az", "account", "show", "-o", "json"], env=env) or {}
        current_id = current.get("id")
        if current_id != sub:
            raise AzError(
                f"SP logged into subscription {current_id}, expected {sub} from secrets JSON"
            )

        payg = ensure_payg_subscription(
            env, sub, create_if_needed=False, display_name=f"Lioxi-{name}"
        )

        for ns in PROVIDERS:
            az_ok(["az", "provider", "register", "--namespace", ns], env=env, timeout=120)
        az_ok(
            [
                "az",
                "feature",
                "register",
                "--namespace",
                "Microsoft.CognitiveServices",
                "--name",
                FIREWORKS_FEATURE,
            ],
            env=env,
        )

        def _create():
            return pick_or_create_account(env, slug, sub)

        acct_name, rg, shown = retry(_create, attempts=8, delay=10, desc="create/find AIServices")
        shown = az_json(
            ["az", "cognitiveservices", "account", "show", "-n", acct_name, "-g", rg, "-o", "json"],
            env=env,
        )
        loc = (shown.get("location") or LOCATION).replace(" ", "").lower()

        dep = retry(
            lambda: ensure_kimi_deployment(env, sub, acct_name, rg, loc),
            attempts=6,
            delay=12,
            desc="deploy K3",
        )
        keys = az_json(
            ["az", "cognitiveservices", "account", "keys", "list", "-n", acct_name, "-g", rg, "-o", "json"],
            env=env,
        )
        props = shown.get("properties") or {}
        endpoint = props.get("endpoint") or ""
        endpoints = props.get("endpoints") or {}
        openai_endpoint = (
            endpoints.get("OpenAI Language Model Instance API")
            or endpoints.get("Azure AI Model Inference API")
            or endpoints.get("AI Foundry API")
            or endpoint
        )
        openai_endpoint = (openai_endpoint or "").replace(
            ".cognitiveservices.azure.com", ".openai.azure.com"
        ).replace(".services.ai.azure.com", ".openai.azure.com")
        email = resolve_email(env, fallback_email)

        return {
            "ok": True,
            "name": name,
            "email": email,
            "azure_openai_endpoint": openai_endpoint or endpoint,
            "api_key": (keys or {}).get("key1"),
            "deployment_name": dep["deployment_name"],
            "model": MODEL_NAME,
            "sku": SKU_NAME,
            "capacity": dep["capacity"],
            "tpm": dep["tpm"],
            "rpm": dep["rpm"],
            "region": loc,
            "resource_group": rg,
            "account_name": acct_name,
            "subscription_id": sub,
            "subscription_name": current.get("name"),
            "quota_limit": dep["quota_limit"],
            "payg": payg,
        }


def looks_like_kimi_stack(acct_name: str, rg: str) -> bool:
    name = (acct_name or "").lower()
    group = (rg or "").lower()
    return "-kimi-" in name and group.startswith("rg-") and group.endswith("-kimi")


def find_kimi_target(
    env: dict[str, str], preferred_name: str = "", preferred_rg: str = ""
) -> tuple[str, str] | None:
    if preferred_name and preferred_rg:
        ok, _ = az_ok(
            [
                "az",
                "cognitiveservices",
                "account",
                "show",
                "-n",
                preferred_name,
                "-g",
                preferred_rg,
                "-o",
                "none",
            ],
            env=env,
        )
        if ok:
            return preferred_name, preferred_rg

    accounts = az_json(["az", "cognitiveservices", "account", "list", "-o", "json"], env=env) or []
    ranked: list[tuple[int, str, str]] = []
    for item in accounts:
        if item.get("kind") != KIND:
            continue
        rid = item.get("id") or ""
        parts = rid.split("/")
        rg = parts[parts.index("resourceGroups") + 1] if "resourceGroups" in parts else ""
        name = item.get("name") or ""
        deps = (
            az_json(
                [
                    "az",
                    "cognitiveservices",
                    "account",
                    "deployment",
                    "list",
                    "-n",
                    name,
                    "-g",
                    rg,
                    "-o",
                    "json",
                ],
                env=env,
            )
            or []
        )
        has_kimi = any(
            d.get("name") == DEPLOYMENT_NAME
            or (((d.get("properties") or {}).get("model") or {}).get("name") == MODEL_NAME)
            for d in deps
        )
        stack = looks_like_kimi_stack(name, rg)
        if not has_kimi and not stack:
            continue
        score = (2 if has_kimi else 0) + (1 if stack else 0)
        ranked.append((score, name, rg))
    if not ranked:
        return None
    ranked.sort(reverse=True)
    return ranked[0][1], ranked[0][2]


def delete_one(acct: dict[str, Any]) -> dict[str, Any]:
    """Remove FW-Kimi-K3. If this session created a dedicated kimi account/RG, delete those too."""
    tenant = acct["AZURE_TENANT_ID"]
    client = acct["AZURE_CLIENT_ID"]
    secret = acct["AZURE_CLIENT_SECRET"]
    sub = acct["AZURE_SUBSCRIPTION_ID"]
    name = acct.get("name") or slugify(acct.get("account_holder") or client)
    preferred_name = acct.get("account_name") or ""
    preferred_rg = acct.get("resource_group") or ""

    with tempfile.TemporaryDirectory(prefix=f"az-del-{slugify(name)}-") as tmp:
        env = isolated_env(Path(tmp))
        login = az_json(
            [
                "az",
                "login",
                "--service-principal",
                "-u",
                client,
                "-p",
                secret,
                "--tenant",
                tenant,
                "-o",
                "json",
            ],
            env=env,
            timeout=60,
        )
        if not login:
            raise AzError("SP login returned empty")
        az_json(["az", "account", "set", "--subscription", sub, "-o", "none"], env=env)
        current = az_json(["az", "account", "show", "-o", "json"], env=env) or {}
        if current.get("id") != sub:
            raise AzError(
                f"SP logged into subscription {current.get('id')}, expected {sub} from secrets JSON"
            )

        target = find_kimi_target(env, preferred_name, preferred_rg)
        if target is None:
            return {
                "ok": True,
                "name": name,
                "subscription_id": sub,
                "subscription_name": current.get("name"),
                "deleted": [],
                "message": "No FW-Kimi-K3 deployment or kimi account found.",
            }

        acct_name, rg = target
        deleted: list[str] = []

        ok, err = az_ok(
            [
                "az",
                "cognitiveservices",
                "account",
                "deployment",
                "delete",
                "-n",
                acct_name,
                "-g",
                rg,
                "--deployment-name",
                DEPLOYMENT_NAME,
                "-o",
                "none",
            ],
            env=env,
            timeout=180,
        )
        if ok or "not found" in err.lower() or "does not exist" in err.lower():
            if ok:
                deleted.append(f"deployment {DEPLOYMENT_NAME}")
        else:
            raise AzError(f"Could not delete deployment {DEPLOYMENT_NAME}: {err}")

        if looks_like_kimi_stack(acct_name, rg):
            projects = []
            try:
                projects = (
                    az_json(
                        [
                            "az",
                            "cognitiveservices",
                            "account",
                            "project",
                            "list",
                            "-n",
                            acct_name,
                            "-g",
                            rg,
                            "-o",
                            "json",
                        ],
                        env=env,
                    )
                    or []
                )
            except AzError:
                projects = []
            for proj in projects:
                pname = (proj or {}).get("name") if isinstance(proj, dict) else None
                if not pname:
                    continue
                ok, err = az_ok(
                    [
                        "az",
                        "cognitiveservices",
                        "account",
                        "project",
                        "delete",
                        "-n",
                        acct_name,
                        "-g",
                        rg,
                        "--project-name",
                        pname,
                        "-o",
                        "none",
                    ],
                    env=env,
                    timeout=180,
                )
                if ok:
                    deleted.append(f"project {pname}")
                elif "not found" not in err.lower() and "does not exist" not in err.lower():
                    raise AzError(f"Could not delete project {pname}: {err}")
            ok, err = az_ok(
                [
                    "az",
                    "cognitiveservices",
                    "account",
                    "delete",
                    "-n",
                    acct_name,
                    "-g",
                    rg,
                    "-o",
                    "none",
                ],
                env=env,
                timeout=300,
            )
            if ok or "not found" in err.lower() or "does not exist" in err.lower():
                if ok:
                    deleted.append(f"account {acct_name}")
            else:
                raise AzError(f"Could not delete account {acct_name}: {err}")
            ok, err = az_ok(
                ["az", "group", "delete", "-n", rg, "--yes", "--no-wait", "-o", "none"],
                env=env,
                timeout=120,
            )
            if ok:
                deleted.append(f"resource group {rg} (delete started)")
            elif "not found" not in err.lower() and "does not exist" not in err.lower():
                deleted.append(f"resource group {rg} left in place: {err[-200:]}")

        return {
            "ok": True,
            "name": name,
            "account_name": acct_name,
            "resource_group": rg,
            "subscription_id": sub,
            "subscription_name": current.get("name"),
            "deleted": deleted,
            "message": "Deleted " + ", ".join(deleted) if deleted else "Nothing left to delete.",
        }


def secrets_row(record: dict[str, Any]) -> dict[str, Any]:
    row = {
        "name": record.get("name"),
        "account_holder": record.get("account_holder") or record.get("email") or "",
        "AZURE_TENANT_ID": record.get("AZURE_TENANT_ID"),
        "AZURE_CLIENT_ID": record.get("AZURE_CLIENT_ID"),
        "AZURE_CLIENT_SECRET": record.get("AZURE_CLIENT_SECRET"),
        "AZURE_SUBSCRIPTION_ID": record.get("AZURE_SUBSCRIPTION_ID"),
        "subscription_name": record.get("subscription_name") or "",
    }
    if record.get("account_name"):
        row["account_name"] = record["account_name"]
    if record.get("service_principal_name"):
        row["service_principal_name"] = record["service_principal_name"]
    return row


DEFAULT_SECRETS_FILE = Path(__file__).resolve().parent.parent / "new_final.json"


def _load_secrets_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise AzError(f"{path} is not valid JSON: {exc}") from exc
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict) and isinstance(data.get("accounts"), list):
        return [item for item in data["accounts"] if isinstance(item, dict)]
    if isinstance(data, dict) and data.get("AZURE_CLIENT_ID"):
        return [data]
    raise AzError(f"{path} must be a JSON array of account objects.")


def upsert_secrets_file(path: Path, row: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    accounts = _load_secrets_list(path)
    sub = row.get("AZURE_SUBSCRIPTION_ID")
    replaced = False
    next_rows: list[dict[str, Any]] = []
    for existing in accounts:
        if sub and existing.get("AZURE_SUBSCRIPTION_ID") == sub:
            next_rows.append(row)
            replaced = True
        else:
            next_rows.append(existing)
    if not replaced:
        next_rows.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(next_rows, indent=2) + "\n")
    os.chmod(path, 0o600)
    return next_rows, replaced


def _login_identity() -> tuple[str, str, str, str]:
    acc = az_json(["az", "account", "show", "-o", "json"])
    user = (acc or {}).get("user") or {}
    if user.get("type") != "user":
        raise AzError(f"Need an Owner user login, got {user.get('type')} {user.get('name')}")
    email = str(user.get("name") or "").strip()
    if not email:
        raise AzError("az account show did not include a user email. Pass --email.")
    sub_id = str(acc.get("id") or "")
    sub_name = str(acc.get("name") or "")
    return email, slugify(email), sub_id, sub_name


def cmd_add_login(args: argparse.Namespace) -> int:
    login_email, login_slug, sub_id, sub_name = _login_identity()
    email = (args.email or login_email).strip()
    name = (args.name or slugify(email) or login_slug).strip()
    out = Path(args.out).expanduser().resolve()
    print(
        json.dumps(
            {
                "login": login_email,
                "subscription_id": sub_id,
                "subscription_name": sub_name,
                "name": name,
                "email": email,
                "out": str(out),
            },
            indent=2,
        ),
        file=sys.stderr,
    )
    with tempfile.NamedTemporaryFile(prefix=f"kimi-{name}-", suffix=".json", delete=False) as handle:
        tmp = Path(handle.name)
    try:
        record = bootstrap(name, email, tmp)
    finally:
        tmp.unlink(missing_ok=True)
    row = secrets_row(record)
    accounts, replaced = upsert_secrets_file(out, row)
    summary = {
        "ok": True,
        "action": "updated" if replaced else "added",
        "name": record.get("name"),
        "account_holder": record.get("account_holder"),
        "account_name": record.get("account_name"),
        "AZURE_TENANT_ID": record.get("AZURE_TENANT_ID"),
        "AZURE_CLIENT_ID": record.get("AZURE_CLIENT_ID"),
        "AZURE_SUBSCRIPTION_ID": record.get("AZURE_SUBSCRIPTION_ID"),
        "subscription_name": record.get("subscription_name"),
        "roles_assigned": record.get("roles_assigned"),
        "roles_failed": record.get("roles_failed"),
        "accounts_in_file": len(accounts),
        "wrote": str(out),
    }
    print(json.dumps(summary, indent=2))
    print(f"secrets {'updated' if replaced else 'added'} in {out} ({len(accounts)} account(s))", file=sys.stderr)
    return 0


def _reset_sp_password(env: dict[str, str] | None, client: str) -> str:
    reset = az_json(
        ["az", "ad", "sp", "credential", "reset", "--id", client, "--years", "1", "-o", "json"],
        env=env,
        timeout=90,
    ) or {}
    password = reset.get("password")
    if not password:
        raise AzError("credential reset returned no password")
    return password


def regenerate_one(acct: dict[str, Any]) -> dict[str, Any]:
    """Mint a new client secret for an existing combined SP. Invalidates the old secret."""
    tenant = acct["AZURE_TENANT_ID"]
    client = acct["AZURE_CLIENT_ID"]
    secret = acct["AZURE_CLIENT_SECRET"]
    sub = acct["AZURE_SUBSCRIPTION_ID"]
    name = acct.get("name") or slugify(acct.get("account_holder") or client)
    holder = acct.get("account_holder") or ""
    sub_name = acct.get("subscription_name") or ""
    errors: list[str] = []

    with tempfile.TemporaryDirectory(prefix=f"az-regen-{slugify(name)}-") as tmp:
        env = isolated_env(Path(tmp))
        try:
            login = az_json(
                [
                    "az",
                    "login",
                    "--service-principal",
                    "-u",
                    client,
                    "-p",
                    secret,
                    "--tenant",
                    tenant,
                    "-o",
                    "json",
                ],
                env=env,
                timeout=60,
            )
            if not login:
                raise AzError("SP login returned empty")
            az_json(["az", "account", "set", "--subscription", sub, "-o", "none"], env=env)
            shown = az_json(["az", "account", "show", "-o", "json"], env=env) or {}
            password = _reset_sp_password(env, client)
            return {
                "ok": True,
                **secrets_row(
                    {
                        "name": name,
                        "account_holder": holder,
                        "AZURE_TENANT_ID": tenant,
                        "AZURE_CLIENT_ID": client,
                        "AZURE_CLIENT_SECRET": password,
                        "AZURE_SUBSCRIPTION_ID": sub,
                        "subscription_name": shown.get("name") or sub_name,
                    }
                ),
            }
        except AzError as exc:
            errors.append(f"as SP: {exc}")

    try:
        acc = az_json(["az", "account", "show", "-o", "json"], timeout=20) or {}
        user = acc.get("user") or {}
        if user.get("type") != "user":
            raise AzError(f"host az login is {user.get('type') or 'missing'}, need Owner user")
        if acc.get("tenantId") != tenant:
            raise AzError(
                f"host az tenant {acc.get('tenantId')} does not match secrets tenant {tenant}"
            )
        password = _reset_sp_password(None, client)
        return {
            "ok": True,
            **secrets_row(
                {
                    "name": name,
                    "account_holder": holder,
                    "AZURE_TENANT_ID": tenant,
                    "AZURE_CLIENT_ID": client,
                    "AZURE_CLIENT_SECRET": password,
                    "AZURE_SUBSCRIPTION_ID": sub,
                    "subscription_name": acc.get("name") or sub_name,
                }
            ),
        }
    except AzError as exc:
        errors.append(f"as Owner: {exc}")

    raise AzError(f"Could not regenerate secret for {name}. " + " | ".join(errors)[-1500:])


def load_accounts(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text())
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "accounts" in data:
        return data["accounts"]
    if isinstance(data, dict) and "AZURE_CLIENT_ID" in data:
        return [data]
    raise AzError("Input JSON must be an account, a list, or {\"accounts\": [...]}")


def cmd_bootstrap(args: argparse.Namespace) -> int:
    record = bootstrap(args.name, args.email, Path(args.out))
    summary = {
        k: record[k]
        for k in (
            "name",
            "account_holder",
            "account_name",
            "AZURE_TENANT_ID",
            "AZURE_CLIENT_ID",
            "AZURE_SUBSCRIPTION_ID",
            "roles_assigned",
            "roles_failed",
            "billing_account_reader",
            "payg",
        )
    }
    print(json.dumps(summary, indent=2))
    print(f"secrets written: {args.out}", file=sys.stderr)
    return 0


def cmd_regenerate(args: argparse.Namespace) -> int:
    accounts = load_accounts(Path(args.input))
    workers = min(args.jobs, max(1, len(accounts)))
    results: list[dict[str, Any]] = [None] * len(accounts)  # type: ignore
    errors = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(regenerate_one, acct): i for i, acct in enumerate(accounts)}
        for fut in as_completed(futs):
            i = futs[fut]
            name = accounts[i].get("name") or accounts[i].get("account_holder") or str(i)
            try:
                results[i] = fut.result()
                print(f"OK {name}", file=sys.stderr)
            except Exception as exc:  # noqa: BLE001
                errors += 1
                results[i] = {"ok": False, "name": name, "error": str(exc)[-1500:]}
                print(f"FAIL {name}: {exc}", file=sys.stderr)

    public = []
    for r in results:
        if r.get("ok"):
            public.append(secrets_row(r))
        else:
            public.append({"ok": False, "name": r.get("name"), "error": r.get("error")})

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(public, indent=2) + "\n")
    os.chmod(out, 0o600)
    print(json.dumps({"ok_count": len(accounts) - errors, "fail_count": errors, "wrote": str(out)}))
    return 1 if errors else 0


def cmd_undeploy(args: argparse.Namespace) -> int:
    accounts = load_accounts(Path(args.input))
    workers = min(args.jobs, max(1, len(accounts)))
    results: list[dict[str, Any]] = [None] * len(accounts)  # type: ignore
    errors = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(delete_one, acct): i for i, acct in enumerate(accounts)}
        for fut in as_completed(futs):
            i = futs[fut]
            name = accounts[i].get("name") or accounts[i].get("account_holder") or str(i)
            try:
                results[i] = fut.result()
                print(f"OK {name} {results[i].get('message')}", file=sys.stderr)
            except Exception as exc:  # noqa: BLE001
                errors += 1
                results[i] = {"ok": False, "name": name, "error": str(exc)[-1500:], "deleted": []}
                print(f"FAIL {name}: {exc}", file=sys.stderr)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"results": results}, indent=2) + "\n")
    print(json.dumps({"ok_count": len(accounts) - errors, "fail_count": errors, "wrote": str(out)}))
    return 1 if errors else 0


def cmd_deploy(args: argparse.Namespace) -> int:
    accounts = load_accounts(Path(args.input))
    workers = min(args.jobs, max(1, len(accounts)))
    results: list[dict[str, Any]] = [None] * len(accounts)  # type: ignore
    errors = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(deploy_one, acct): i for i, acct in enumerate(accounts)}
        for fut in as_completed(futs):
            i = futs[fut]
            name = accounts[i].get("name") or accounts[i].get("account_holder") or str(i)
            try:
                results[i] = fut.result()
                print(f"OK {name} capacity={results[i]['capacity']} endpoint={results[i]['azure_openai_endpoint']}", file=sys.stderr)
            except Exception as exc:  # noqa: BLE001
                errors += 1
                results[i] = {"ok": False, "name": name, "error": str(exc)[-1500:]}
                print(f"FAIL {name}: {exc}", file=sys.stderr)

    public = []
    for r in results:
        if r.get("ok"):
            public.append(
                {
                    "email": r.get("email"),
                    "azure_openai_endpoint": r.get("azure_openai_endpoint"),
                    "api_key": r.get("api_key"),
                    "deployment_name": r.get("deployment_name"),
                    "tpm": r.get("tpm"),
                    "rpm": r.get("rpm"),
                    "region": r.get("region"),
                    "account_name": r.get("account_name"),
                    "name": r.get("name"),
                    "subscription_id": r.get("subscription_id"),
                    "subscription_name": r.get("subscription_name"),
                    "payg": r.get("payg"),
                }
            )
        else:
            public.append({"ok": False, "name": r.get("name"), "error": r.get("error")})

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"results": public, "full": results}
    out.write_text(json.dumps(payload, indent=2) + "\n")
    os.chmod(out, 0o600)
    print(json.dumps({"results": public}, indent=2))
    print(f"wrote {out}", file=sys.stderr)
    return 1 if errors else 0


def cmd_ensure_payg(_args: argparse.Namespace) -> int:
    acc = az_json(["az", "account", "show", "-o", "json"]) or {}
    user = acc.get("user") or {}
    if user.get("type") != "user":
        raise AzError("ensure-payg needs an Owner user login")
    info = ensure_payg_subscription(
        None, acc["id"], create_if_needed=True, display_name="Pay-As-You-Go"
    )
    print(json.dumps(info, indent=2))
    return 0


def cmd_bootstrap_and_deploy(args: argparse.Namespace) -> int:
    secrets_path = Path(args.out_secrets)
    rc = cmd_bootstrap(
        argparse.Namespace(name=args.name, email=args.email, out=str(secrets_path))
    )
    if rc != 0:
        return rc
    print("waiting 20s for RBAC to propagate …", file=sys.stderr)
    time.sleep(20)
    return cmd_deploy(
        argparse.Namespace(input=str(secrets_path), out=args.out_results, jobs=1)
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("bootstrap", help="Create combined viewer+admin SP from current Owner login")
    b.add_argument("--name", required=True)
    b.add_argument("--email", required=True)
    b.add_argument("--out", required=True)
    b.set_defaults(func=cmd_bootstrap)

    add = sub.add_parser(
        "add-login",
        help="Create/reset the combined SP for the current Owner az login and upsert into new_final.json",
    )
    add.add_argument("--name", default="", help="Slug stored as name. Default: email local-part")
    add.add_argument("--email", default="", help="account_holder. Default: az login user")
    add.add_argument("--out", default=str(DEFAULT_SECRETS_FILE), help="Shared JSON array to append/update")
    add.set_defaults(func=cmd_add_login)

    d = sub.add_parser("deploy", help="Deploy FW-Kimi-K3 from a JSON array of SP secrets")
    d.add_argument("--input", required=True)
    d.add_argument("--out", required=True)
    d.add_argument("--jobs", type=int, default=6)
    d.set_defaults(func=cmd_deploy)

    rg = sub.add_parser("regenerate", help="Reset client secrets for a JSON array of existing SPs")
    rg.add_argument("--input", required=True)
    rg.add_argument("--out", required=True)
    rg.add_argument("--jobs", type=int, default=4)
    rg.set_defaults(func=cmd_regenerate)

    u = sub.add_parser("undeploy", help="Delete FW-Kimi-K3 (and dedicated kimi account/RG if we created it)")
    u.add_argument("--input", required=True)
    u.add_argument("--out", required=True)
    u.add_argument("--jobs", type=int, default=4)
    u.set_defaults(func=cmd_undeploy)

    ep = sub.add_parser("ensure-payg", help="Check current Owner login and upgrade/create PAYG if blocked")
    ep.set_defaults(func=cmd_ensure_payg)

    bd = sub.add_parser("bootstrap-and-deploy", help="Owner bootstrap then immediately deploy as the new SP")
    bd.add_argument("--name", required=True)
    bd.add_argument("--email", required=True)
    bd.add_argument("--out-secrets", required=True)
    bd.add_argument("--out-results", required=True)
    bd.set_defaults(func=cmd_bootstrap_and_deploy)
    return p


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.func(args)
    except AzError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
