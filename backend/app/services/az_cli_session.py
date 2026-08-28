"""Per-submit-session Azure CLI isolation.

Each join session gets its own AZURE_CONFIG_DIR under /tmp/az-submit/{uuid}.
Commands for one session are serialized. A global semaphore caps concurrent az
processes so ARM/login storms cannot fork dozens of CLIs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
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
TOKEN_RE = re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
SECRET_FLAGS = {"-p", "--password", "--secret", "--client-secret"}
TENANT_GUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.I,
)
SESSION_ID_RE = TENANT_GUID_RE
FAILED_AGAINST_TENANT_RE = re.compile(
    r"Authentication failed against tenant\s+([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.I,
)
TENANT_NAME_LINE_RE = re.compile(
    r"^\s*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\s+'[^']*'",
    re.I | re.M,
)
# Personal Microsoft account home tenant — not an Azure directory you can az login --tenant to.
MSA_TENANTS = {
    "9188040d-6c67-4c5b-b112-36a304b66dad",
    "f8cdef31-a31e-4b4a-93e4-5f571e91255a",
}
MAX_TENANT_RETRIES = 3
SECURITY_DEFAULTS_MESSAGE = "Microsoft needs another sign-in. Try again and enter the new code when it appears."


class AzCliError(RuntimeError):
    pass


def scrub_az_text(text: str) -> str:
    raw = PASSWORD_JSON_RE.sub(r"\1***\2", text or "")
    raw = TOKEN_RE.sub("[token]", raw)
    return raw[-1500:]


def format_az_cmd(cmd: list[str]) -> str:
    parts: list[str] = []
    skip_value = False
    for index, arg in enumerate(cmd):
        if skip_value:
            parts.append("***")
            skip_value = False
            continue
        if index == 0:
            parts.append("az" if arg == "az" or arg.endswith("/az") else arg)
            continue
        key = arg.split("=", 1)[0]
        if arg in SECRET_FLAGS or key in SECRET_FLAGS:
            if "=" in arg:
                parts.append(f"{key}=***")
            else:
                parts.append(arg)
                skip_value = True
            continue
        parts.append(arg)
    return "$ " + " ".join(parts)


def relay_output_line(text: str) -> str | None:
    line = scrub_az_text(text).strip()
    if not line:
        return None
    if line[0] in "{[":
        return None
    if len(line) > 240:
        line = line[:237] + "..."
    return line


def _module():
    from app.services.kimi_deploy_service import load_deploy_module

    return load_deploy_module()


ProgressFn = Callable[[dict[str, Any]], Awaitable[None]]


def _chmod700(path: Path) -> None:
    try:
        os.chmod(path, 0o700)
    except OSError:
        return


def session_config_dir(session_id: str) -> Path:
    """Join Azure CLI state lives only under /tmp/az-submit/<uuid>."""
    sid = (session_id or "").strip().lower()
    if not SESSION_ID_RE.fullmatch(sid):
        raise AzCliError("Invalid join session.")
    CONFIG_ROOT.mkdir(parents=True, exist_ok=True)
    _chmod700(CONFIG_ROOT)
    path = (CONFIG_ROOT / sid).resolve()
    if path.parent != CONFIG_ROOT.resolve():
        raise AzCliError("Invalid join session.")
    path.mkdir(parents=True, exist_ok=True)
    _chmod700(path)
    return path


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


def normalize_tenant_id(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text or not TENANT_GUID_RE.fullmatch(text):
        return None
    lowered = text.lower()
    if lowered in MSA_TENANTS:
        return None
    return lowered


def parse_blocked_tenants(text: str) -> list[str]:
    """Tenant IDs Azure CLI named after a security-defaults / ARM token failure.

    Do not scan every GUID in the buffer — Trace ID and Correlation ID are also UUIDs.
    """
    ordered: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        tid = normalize_tenant_id(raw)
        if not tid or tid in seen:
            return
        seen.add(tid)
        ordered.append(tid)

    blob = text or ""
    for match in FAILED_AGAINST_TENANT_RE.finditer(blob):
        add(match.group(1))
    for match in TENANT_NAME_LINE_RE.finditer(blob):
        add(match.group(1))
    return ordered


def security_defaults_blocked(text: str) -> bool:
    lowered = (text or "").lower()
    return "aadsts530035" in lowered or "blocked by security defaults" in lowered


def humanize_login_failure(text: str) -> str:
    if security_defaults_blocked(text) or parse_blocked_tenants(text):
        return SECURITY_DEFAULTS_MESSAGE
    lowered = (text or "").lower()
    if "no subscriptions found" in lowered:
        return "No Azure subscription was found on this Microsoft account."
    return "Could not finish Microsoft sign-in. Try again."


class AzCliSession:
    def __init__(self, session_id: str, config_dir: Path | None = None) -> None:
        self.session_id = (session_id or "").strip().lower()
        self.config_dir = session_config_dir(self.session_id)
        self.lock = asyncio.Lock()
        self.login_proc: asyncio.subprocess.Process | None = None
        self._log: ProgressFn | None = None
        _ = config_dir

    def set_log(self, fn: ProgressFn | None) -> None:
        self._log = fn

    async def _relay(self, line: str, kind: str = "out") -> None:
        text = (line or "").strip()
        if not text or self._log is None:
            return
        await self._log(
            {
                "type": "terminal",
                "line": text[:400],
                "kind": kind,
                "session_id": self.session_id,
            }
        )

    async def _relay_cmd(self, cmd: list[str]) -> None:
        await self._relay(format_az_cmd(cmd), "cmd")

    def env(self) -> dict[str, str]:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_cli_config()
        return _module().isolated_env(self.config_dir)

    def _ensure_cli_config(self) -> None:
        path = self.config_dir / "config"
        if path.exists():
            return
        path.write_text(
            "[core]\nlogin_experience_v2 = off\ncollect_telemetry = false\nencrypt_token_cache = false\n",
            encoding="utf-8",
        )

    def _clear_login_cache(self) -> None:
        for name in (
            "msal_token_cache.json",
            "msal_token_cache.bin",
            "msal_http_cache.bin",
            "azureProfile.json",
            "accessTokens.json",
        ):
            target = self.config_dir / name
            try:
                target.unlink(missing_ok=True)
            except OSError:
                continue

    async def _run_json(self, cmd: list[str], timeout: int = 180) -> Any:
        mod = _module()
        await self._relay_cmd(cmd)
        async with self.lock:
            async with _GLOBAL_SEM:
                try:
                    data = await asyncio.to_thread(mod.az_json, cmd, self.env(), timeout)
                except Exception as exc:  # noqa: BLE001
                    detail = scrub_az_text(str(exc))
                    await self._relay(relay_output_line(detail) or "failed", "err")
                    raise AzCliError(detail) from exc
        await self._relay("ok", "ok")
        return data

    async def _run_ok(self, cmd: list[str], timeout: int = 180) -> tuple[bool, str]:
        mod = _module()
        await self._relay_cmd(cmd)
        async with self.lock:
            async with _GLOBAL_SEM:
                try:
                    ok, err = await asyncio.to_thread(mod.az_ok, cmd, self.env(), timeout)
                    err = scrub_az_text(err)
                except Exception as exc:  # noqa: BLE001
                    err = scrub_az_text(str(exc))
                    await self._relay(relay_output_line(err) or "failed", "err")
                    return False, err
        if ok:
            await self._relay("ok", "ok")
        else:
            await self._relay(relay_output_line(err) or "failed", "err")
        return ok, err

    async def device_login(
        self,
        on_event: ProgressFn,
        timeout: int = LOGIN_TIMEOUT_SEC,
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """One Microsoft device login. Tenant ID is parsed from Azure's response, then ARM is retried silently."""
        az = shutil.which("az")
        if not az:
            raise AzCliError("Azure CLI (az) is not on the backend PATH.")
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.set_log(on_event)

        hint = normalize_tenant_id(tenant_id)
        accounts, buffer, ok = await self._device_login_once(
            on_event,
            timeout=timeout,
            tenant_id=hint,
            retry=False,
            allow_no_subscriptions=True,
        )
        last_buffer = buffer
        listed = await self._accounts_if_user(accounts if ok else [])
        if listed:
            return listed

        tenants = await self._discover_tenant_ids(buffer, hint)
        for tid in tenants:
            silent = await self._try_silent_tenant(tid)
            if silent:
                logger.info("join login used parsed tenant %s silently session=%s", tid, self.session_id)
                return silent

        for tid in tenants:
            await on_event(
                {
                    "type": "device_code",
                    "user_code": "",
                    "verification_uri": "https://microsoft.com/devicelogin",
                    "message": "Microsoft needs one more sign-in. A new code is coming — use that one.",
                }
            )
            accounts, buffer, ok = await self._device_login_once(
                on_event,
                timeout=timeout,
                tenant_id=tid,
                retry=True,
                allow_no_subscriptions=True,
            )
            last_buffer = buffer
            listed = await self._accounts_if_user(accounts if ok else [])
            if listed:
                return listed
            silent = await self._try_silent_tenant(tid)
            if silent:
                return silent

        if not security_defaults_blocked(last_buffer) and not parse_blocked_tenants(last_buffer):
            raise AzCliError("No Azure subscription was found on this Microsoft account.")
        raise AzCliError(humanize_login_failure(last_buffer))

    async def _accounts_if_user(self, accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not accounts:
            try:
                accounts = await self.account_list()
            except AzCliError:
                return []
        if not accounts:
            return []
        try:
            identity = await self.account_show()
        except AzCliError:
            return accounts
        user = identity.get("user") or {}
        if user.get("type") not in {"user", None, ""}:
            raise AzCliError(
                "Sign in with the Microsoft account that owns the Azure subscription, not an app login."
            )
        return accounts

    async def _discover_tenant_ids(self, buffer: str, hint: str | None) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()

        def add(raw: str | None) -> None:
            tid = normalize_tenant_id(raw)
            if not tid or tid in seen:
                return
            seen.add(tid)
            ordered.append(tid)

        add(hint)
        for tid in parse_blocked_tenants(buffer):
            add(tid)
        try:
            identity = await self.account_show()
        except AzCliError:
            identity = {}
        add(str((identity or {}).get("tenantId") or ""))
        for tid in await self._graph_tenant_ids():
            add(tid)
        return ordered[:MAX_TENANT_RETRIES]

    async def _graph_tenant_ids(self) -> list[str]:
        ok, text = await self._run_ok(
            [
                "az",
                "rest",
                "--method",
                "get",
                "--url",
                "https://graph.microsoft.com/v1.0/organization",
                "--resource",
                "https://graph.microsoft.com/",
                "-o",
                "json",
            ],
            timeout=45,
        )
        if not ok:
            return []
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return []
        rows = data.get("value") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            return []
        out: list[str] = []
        for row in rows:
            if isinstance(row, dict):
                tid = normalize_tenant_id(str(row.get("id") or ""))
                if tid:
                    out.append(tid)
        return out

    async def _try_silent_tenant(self, tenant_id: str) -> list[dict[str, Any]]:
        ok, _err = await self._run_ok(
            [
                "az",
                "account",
                "get-access-token",
                "--tenant",
                tenant_id,
                "--resource",
                "https://management.azure.com/",
                "-o",
                "json",
            ],
            timeout=45,
        )
        if not ok:
            return []
        try:
            listed = await self.account_list()
        except AzCliError:
            return []
        return [item for item in listed if isinstance(item, dict)]

    async def _device_login_once(
        self,
        on_event: ProgressFn,
        *,
        timeout: int,
        tenant_id: str | None,
        retry: bool,
        allow_no_subscriptions: bool = False,
    ) -> tuple[list[dict[str, Any]], str, bool]:
        az = shutil.which("az")
        if not az:
            raise AzCliError("Azure CLI (az) is not on the backend PATH.")
        cmd = [az, "login", "--use-device-code"]
        if tenant_id:
            cmd.extend(["--tenant", tenant_id])
        if allow_no_subscriptions:
            cmd.append("--allow-no-subscriptions")
        cmd.extend(["-o", "json"])
        await self._relay_cmd(cmd)
        env = self.env()
        async with _GLOBAL_SEM:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
        self.login_proc = proc
        buffer = ""
        pending = ""
        device_sent = False
        message = (
            "Microsoft needs one more sign-in. Open the page and enter this new code."
            if retry
            else "Open the Microsoft device login page and enter this code."
        )

        async def _read() -> None:
            nonlocal buffer, pending, device_sent
            assert proc.stdout is not None
            while True:
                chunk = await proc.stdout.read(512)
                if not chunk:
                    break
                decoded = chunk.decode("utf-8", errors="replace")
                buffer += decoded
                pending += decoded
                while "\n" in pending:
                    raw_line, pending = pending.split("\n", 1)
                    shown = relay_output_line(raw_line)
                    if shown:
                        await self._relay(shown, "out")
                if not device_sent:
                    uri, code = parse_device_prompt(buffer)
                    if uri and code:
                        device_sent = True
                        await on_event(
                            {
                                "type": "device_code",
                                "user_code": code,
                                "verification_uri": uri,
                                "message": message,
                            }
                        )
            leftover = relay_output_line(pending)
            if leftover:
                await self._relay(leftover, "out")

        try:
            await asyncio.wait_for(_read(), timeout=timeout)
        except TimeoutError as exc:
            await self.kill_login()
            raise AzCliError("Sign-in timed out. Try again.") from exc
        finally:
            self.login_proc = None

        rc = proc.returncode
        if rc is None:
            rc = await proc.wait()
        if rc != 0:
            logger.info(
                "az login failed session=%s tenant=%s rc=%s defaults_block=%s",
                self.session_id,
                tenant_id or "organizations",
                rc,
                security_defaults_blocked(buffer),
            )
            await self._relay(f"exit {rc}", "err")
            return [], buffer, False
        await self._relay("ok", "ok")
        try:
            listed = await self.account_list()
        except AzCliError:
            listed = []
        return listed, buffer, True

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
            await self._relay(
                f"$ az role assignment create --assignee-object-id {object_id} --role {role} --scope /subscriptions/{subscription_id}",
                "cmd",
            )
            async with self.lock:
                async with _GLOBAL_SEM:
                    try:
                        ok, err = await asyncio.to_thread(
                            mod.assign_role_arm, self.env(), object_id, role, subscription_id
                        )
                    except Exception as exc:  # noqa: BLE001
                        err = scrub_az_text(str(exc))
                        await self._relay(relay_output_line(err) or "failed", "err")
                        return False, err
            if ok or "already exists" in err.lower() or "exist" in err.lower():
                await self._relay("ok", "ok")
                return True, ""
            await self._relay(relay_output_line(err) or "failed", "err")
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
    sid = (session_id or "").strip().lower()
    async with _sessions_guard:
        existing = _sessions.get(sid)
        if existing is not None:
            return existing
        session = AzCliSession(sid, Path(config_dir) if config_dir else None)
        _sessions[sid] = session
        return session


async def drop_az_session(session_id: str) -> None:
    sid = (session_id or "").strip().lower()
    async with _sessions_guard:
        session = _sessions.pop(sid, None)
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
