"""Per-submit-session Azure CLI isolation.

Each join session gets its own AZURE_CONFIG_DIR under /tmp/az-submit/{uuid}.
Commands for one session are serialized. A global semaphore caps concurrent az
processes so ARM/login storms cannot fork dozens of CLIs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

CONFIG_ROOT = Path("/tmp/az-submit")
LOGIN_TIMEOUT_SEC = 15 * 60
_GLOBAL_SEM = asyncio.Semaphore(8)
_sessions: dict[str, "AzCliSession"] = {}
_sessions_guard = asyncio.Lock()

DEVICE_URI_RE = re.compile(
    r"https://(?:www\.)?(?:microsoft\.com/devicelogin|login\.microsoft(?:online)?\.com/device(?:s)?|aka\.ms/devicelogin)",
    re.I,
)
DEVICE_CODE_RE = re.compile(
    r"(?:enter the code|enter code|code is|code:)\s+([A-Z0-9][A-Z0-9-]{7,20})",
    re.I,
)
PASSWORD_JSON_RE = re.compile(r'("password"\s*:\s*")[^"]+(")', re.I)


class AzCliError(RuntimeError):
    pass


def scrub_az_text(text: str) -> str:
    raw = PASSWORD_JSON_RE.sub(r"\1***\2", text or "")
    return raw[-1500:]


def _module():
    from app.services.kimi_deploy_service import load_deploy_module

    return load_deploy_module()


ProgressFn = Callable[[dict[str, Any]], Awaitable[None]]


def parse_device_prompt(text: str) -> tuple[str | None, str | None]:
    uri = None
    match = DEVICE_URI_RE.search(text or "")
    if match:
        uri = match.group(0)
    code = None
    code_match = DEVICE_CODE_RE.search(text or "")
    if code_match:
        code = code_match.group(1).strip().upper()
    if not code:
        loose = re.search(r"\b([A-Z0-9]{8,9})\b", (text or "").upper())
        if loose and uri:
            code = loose.group(1)
    return uri, code


class AzCliSession:
    def __init__(self, session_id: str, config_dir: Path | None = None) -> None:
        self.session_id = session_id
        self.config_dir = config_dir or (CONFIG_ROOT / session_id)
        self.lock = asyncio.Lock()
        self.login_proc: asyncio.subprocess.Process | None = None

    def env(self) -> dict[str, str]:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        return _module().isolated_env(self.config_dir)

    async def _run_json(self, cmd: list[str], timeout: int = 180) -> Any:
        mod = _module()
        async with self.lock:
            async with _GLOBAL_SEM:
                try:
                    return await asyncio.to_thread(mod.az_json, cmd, self.env(), timeout)
                except Exception as exc:  # noqa: BLE001
                    raise AzCliError(scrub_az_text(str(exc))) from exc

    async def _run_ok(self, cmd: list[str], timeout: int = 180) -> tuple[bool, str]:
        mod = _module()
        async with self.lock:
            async with _GLOBAL_SEM:
                try:
                    ok, err = await asyncio.to_thread(mod.az_ok, cmd, self.env(), timeout)
                    return ok, scrub_az_text(err)
                except Exception as exc:  # noqa: BLE001
                    return False, scrub_az_text(str(exc))

    async def device_login(self, on_event: ProgressFn, timeout: int = LOGIN_TIMEOUT_SEC) -> list[dict[str, Any]]:
        az = shutil.which("az")
        if not az:
            raise AzCliError("Azure CLI (az) is not on the backend PATH.")
        self.config_dir.mkdir(parents=True, exist_ok=True)
        env = self.env()
        async with _GLOBAL_SEM:
            proc = await asyncio.create_subprocess_exec(
                az,
                "login",
                "--use-device-code",
                "-o",
                "json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
        self.login_proc = proc
        buffer = ""
        device_sent = False

        async def _read() -> None:
            nonlocal buffer, device_sent
            assert proc.stdout is not None
            while True:
                chunk = await proc.stdout.read(512)
                if not chunk:
                    break
                buffer += chunk.decode("utf-8", errors="replace")
                if not device_sent:
                    uri, code = parse_device_prompt(buffer)
                    if uri and code:
                        device_sent = True
                        await on_event(
                            {
                                "type": "device_code",
                                "user_code": code,
                                "verification_uri": uri,
                                "message": "Open the Microsoft device login page and enter this code.",
                            }
                        )

        try:
            await asyncio.wait_for(_read(), timeout=timeout)
        except TimeoutError as exc:
            await self.kill_login()
            raise AzCliError("Azure sign-in timed out. Start again.") from exc
        finally:
            self.login_proc = None

        rc = proc.returncode
        if rc is None:
            rc = await proc.wait()
        if rc != 0:
            raise AzCliError(f"Azure sign-in failed: {scrub_az_text(buffer) or f'exit {rc}'}")

        identity = await self.account_show()
        user = identity.get("user") or {}
        if user.get("type") != "user":
            raise AzCliError(
                f"Need an Owner user login, got {user.get('type') or 'unknown'} {user.get('name') or ''}".strip()
            )
        return await self.account_list()

    async def kill_login(self) -> None:
        proc = self.login_proc
        self.login_proc = None
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.kill()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except (TimeoutError, ProcessLookupError):
            return

    async def account_show(self) -> dict[str, Any]:
        data = await self._run_json(["az", "account", "show", "-o", "json"], timeout=30)
        return data if isinstance(data, dict) else {}

    async def account_list(self) -> list[dict[str, Any]]:
        data = await self._run_json(["az", "account", "list", "-o", "json"], timeout=45)
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        return []

    async def set_subscription(self, subscription_id: str) -> None:
        await self._run_json(
            ["az", "account", "set", "--subscription", subscription_id, "-o", "none"],
            timeout=30,
        )

    async def list_sps_by_name(self, display_name: str) -> list[dict[str, Any]]:
        data = await self._run_json(
            [
                "az",
                "ad",
                "sp",
                "list",
                "--display-name",
                display_name,
                "--query",
                "[].{appId:appId,id:id,displayName:displayName}",
                "-o",
                "json",
            ],
            timeout=60,
        )
        rows = data if isinstance(data, list) else []
        wanted = display_name.strip().lower()
        out: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("displayName") or "").strip().lower()
            if name == wanted:
                out.append(row)
        return out

    async def create_sp(self, display_name: str) -> tuple[str, str]:
        created = await self._run_json(
            [
                "az",
                "ad",
                "sp",
                "create-for-rbac",
                "--name",
                display_name,
                "--skip-assignment",
                "--years",
                "1",
                "-o",
                "json",
            ],
            timeout=90,
        )
        if not isinstance(created, dict):
            raise AzCliError("create-for-rbac returned no application")
        app_id = str(created.get("appId") or "").strip()
        secret = str(created.get("password") or "").strip()
        if not app_id or not secret:
            raise AzCliError("create-for-rbac did not return an app id and secret")
        return app_id, secret

    async def reset_sp_password(self, app_id: str) -> str:
        reset = await self._run_json(
            ["az", "ad", "sp", "credential", "reset", "--id", app_id, "--years", "1", "-o", "json"],
            timeout=90,
        )
        password = str((reset or {}).get("password") or "").strip() if isinstance(reset, dict) else ""
        if not password:
            raise AzCliError("credential reset returned no password")
        return password

    async def sp_object_id(self, app_id: str) -> str | None:
        try:
            oid = await self._run_json(
                ["az", "ad", "sp", "show", "--id", app_id, "--query", "id", "-o", "json"],
                timeout=45,
            )
        except AzCliError:
            return None
        if oid is None:
            return None
        return str(oid).strip() or None

    async def add_sp_as_app_owner(self, app_id: str, object_id: str) -> None:
        await self._run_ok(
            ["az", "ad", "app", "owner", "add", "--id", app_id, "--owner-object-id", object_id],
            timeout=45,
        )

    async def assign_role(
        self,
        app_id: str,
        role: str,
        subscription_id: str,
        *,
        object_id: str | None = None,
        timeout: int = 45,
    ) -> tuple[bool, str]:
        mod = _module()
        if object_id:
            async with self.lock:
                async with _GLOBAL_SEM:
                    try:
                        ok, err = await asyncio.to_thread(
                            mod.assign_role_arm, self.env(), object_id, role, subscription_id
                        )
                    except Exception as exc:  # noqa: BLE001
                        return False, scrub_az_text(str(exc))
            if ok or "already exists" in err.lower() or "exist" in err.lower():
                return True, ""
            return False, err
        scope = f"/subscriptions/{subscription_id}"
        cmd = mod.role_assignment_cmd(role, scope, app_id, None)
        ok, err = await self._run_ok(cmd, timeout=timeout)
        if ok or "already exists" in err.lower() or "exist" in err.lower():
            return True, ""
        return False, err

    async def assign_billing_reader(self, object_id: str, tenant_id: str) -> tuple[bool, str | None]:
        mod = _module()
        role_id = getattr(mod, "BILLING_ACCOUNT_READER", "50000000-aaaa-bbbb-cccc-100000000002")
        try:
            bas = await self._run_json(["az", "billing", "account", "list", "-o", "json"], timeout=60)
        except AzCliError as exc:
            return False, scrub_az_text(str(exc))
        if isinstance(bas, dict):
            bas = bas.get("value") or []
        if not bas:
            return False, "No billing account found"
        ba_name = (bas[0] or {}).get("name") if isinstance(bas[0], dict) else None
        if not ba_name:
            return False, "Billing account has no name"
        body = json.dumps(
            {
                "principalId": object_id,
                "principalTenantId": tenant_id,
                "roleDefinitionId": (
                    f"/providers/Microsoft.Billing/billingAccounts/{ba_name}"
                    f"/billingRoleDefinitions/{role_id}"
                ),
            }
        )
        url = (
            f"https://management.azure.com/providers/Microsoft.Billing/billingAccounts/"
            f"{ba_name}/createBillingRoleAssignment?api-version=2024-04-01"
        )
        ok, err = await self._run_ok(
            ["az", "rest", "--method", "post", "--url", url, "--body", body, "-o", "json"],
            timeout=60,
        )
        return ok, None if ok else err

    def cleanup(self) -> None:
        try:
            if self.config_dir.exists():
                shutil.rmtree(self.config_dir, ignore_errors=True)
        except OSError as exc:
            logger.warning("Could not remove az config dir %s: %s", self.config_dir, exc)


async def get_az_session(session_id: str, config_dir: str | None = None) -> AzCliSession:
    async with _sessions_guard:
        existing = _sessions.get(session_id)
        if existing is not None:
            return existing
        path = Path(config_dir) if config_dir else None
        session = AzCliSession(session_id, path)
        _sessions[session_id] = session
        return session


async def drop_az_session(session_id: str) -> None:
    async with _sessions_guard:
        session = _sessions.pop(session_id, None)
    if session is None:
        return
    await session.kill_login()
    session.cleanup()


def cleanup_stale_config_dirs(max_age_seconds: int = 2 * 60 * 60) -> None:
    import time

    if not CONFIG_ROOT.is_dir():
        return
    now = time.time()
    for child in CONFIG_ROOT.iterdir():
        try:
            age = now - child.stat().st_mtime
        except OSError:
            continue
        if age > max_age_seconds:
            shutil.rmtree(child, ignore_errors=True)
