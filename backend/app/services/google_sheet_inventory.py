"""Upsert the Sheet1 tab after a successful K3 / NewAPI write.

Live Sheet1 headers (row 1):
Sno | (person name, unlabeled) | Email | Endpoint | Api Key | TPM | Proxy_Name | Pool | Oldseven ($) | LingXin ($)

Writes Sno, person, Email, Endpoint, TPM, Proxy_Name, Pool. Api Key is "-" on new
rows and never overwritten. Oldseven/LingXin are left alone.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import socket
import threading
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.schemas.kimi_deploy import KimiDeployResult
from app.services.owner_tag import resource_key

logger = logging.getLogger(__name__)

REQUIRED_HEADERS = ["Sno", "Email", "Endpoint", "TPM", "Proxy_Name", "Pool"]
_HEADER_ALIASES = {
    "sno": "Sno",
    "email": "Email",
    "emailaddress": "Email",
    "endpoint": "Endpoint",
    "tpm": "TPM",
    "proxyname": "Proxy_Name",
    "newapiname": "Proxy_Name",
    "pool": "Pool",
    "poolcreditsgrant": "Pool",
    "creditsgrant": "Pool",
    "apikey": "API_Key",
    "person": "Person",
    "personassociated": "Person",
    "ownertag": "Person",
    "name": "Person",
}

_lock = threading.Lock()
_ipv4_patched = False


def _force_ipv4() -> None:
    """Campus DNS returns IPv6 for Google APIs that this network cannot reach."""
    global _ipv4_patched
    if _ipv4_patched:
        return
    orig = socket.getaddrinfo

    def ipv4_only(host, port, family=0, type=0, proto=0, flags=0):
        return orig(host, port, socket.AF_INET, type, proto, flags)

    socket.getaddrinfo = ipv4_only
    _ipv4_patched = True


def _norm_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").strip().lower())


def format_compact(value: float | int | None) -> str:
    """10000 → 10k, 500000 → 500k. Leaves non-round thousands as-is."""
    if value is None:
        return ""
    number = float(value)
    if not number or number < 0:
        return ""
    if number >= 1_000_000 and number % 1_000_000 == 0:
        return f"{int(number // 1_000_000)}M"
    if number >= 1_000 and number % 1_000 == 0:
        return f"{int(number // 1_000)}k"
    if number == int(number):
        return str(int(number))
    return str(number)


def _canonical_endpoint(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    return raw.rstrip("/") + "/"


def configured() -> bool:
    settings = get_settings()
    if not (settings.google_sheets_spreadsheet_id or "").strip():
        return False
    path = (settings.google_sheets_credentials_path or "").strip()
    blob = (settings.google_sheets_credentials_json or "").strip()
    return bool(blob) or (bool(path) and Path(path).is_file())


def _credentials_info() -> dict[str, Any]:
    settings = get_settings()
    blob = (settings.google_sheets_credentials_json or "").strip()
    if blob:
        data = json.loads(blob)
        if not isinstance(data, dict):
            raise ValueError("GOOGLE_SHEETS_CREDENTIALS_JSON must be a service-account object.")
        return data
    path = Path((settings.google_sheets_credentials_path or "").strip())
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Google Sheets credentials file must be a service-account JSON object.")
    return data


def _open_worksheet():
    import gspread
    from google.oauth2.service_account import Credentials

    _force_ipv4()
    settings = get_settings()
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(_credentials_info(), scopes=scopes)
    client = gspread.authorize(creds)
    book = client.open_by_key(settings.google_sheets_spreadsheet_id.strip())
    tab = (settings.google_sheets_tab or "Sheet1").strip() or "Sheet1"
    try:
        return book.worksheet(tab)
    except gspread.exceptions.WorksheetNotFound as exc:
        names = ", ".join(ws.title for ws in book.worksheets()) or "(none)"
        raise SheetSyncError(f'Tab "{tab}" was not found. Available: {names}') from exc


def _column_map(header_row: list[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for index, cell in enumerate(header_row):
        key = _HEADER_ALIASES.get(_norm_header(cell))
        if key and key not in mapping:
            mapping[key] = index
    if "Person" not in mapping:
        sno = mapping.get("Sno")
        email = mapping.get("Email")
        for index, cell in enumerate(header_row):
            if str(cell).strip():
                continue
            if sno is not None and index <= sno:
                continue
            if email is not None and index >= email:
                continue
            mapping["Person"] = index
            break
    return mapping


def _cell(row: list[str], mapping: dict[str, int], name: str) -> str:
    index = mapping.get(name)
    if index is None or index >= len(row):
        return ""
    return (row[index] or "").strip()


def _ensure_headers(worksheet) -> dict[str, int]:
    rows = worksheet.get_all_values()
    header_row = rows[0] if rows else []
    mapping = _column_map(header_row)
    missing = [name for name in REQUIRED_HEADERS if name not in mapping]
    if missing:
        found = ", ".join(cell for cell in header_row if str(cell).strip()) or "(empty row 1)"
        raise SheetSyncError(
            "Sheet1 is missing columns: "
            + ", ".join(missing)
            + f". Row 1 has: {found}"
        )
    return mapping


def _row_values(mapping: dict[str, int], values: dict[str, str], current: list[str] | None = None) -> list[str]:
    width = max(mapping.values()) + 1
    if current:
        width = max(width, len(current))
        out = list(current) + [""] * (width - len(current))
    else:
        out = [""] * width
    for name, index in mapping.items():
        if name in values:
            out[index] = values[name]
    return out


def _parse_sno(value: str) -> int:
    text = (value or "").strip()
    if not text:
        return 0
    match = re.match(r"^(\d+)", text)
    return int(match.group(1)) if match else 0


def _inventory_row(result: KimiDeployResult) -> dict[str, str] | None:
    endpoint = _canonical_endpoint(result.azure_openai_endpoint) or _canonical_endpoint(result.account_name)
    if not resource_key(endpoint) and not resource_key(result.account_name):
        return None
    if not endpoint and result.account_name:
        endpoint = _canonical_endpoint(f"{result.account_name}.openai.azure.com")
    tpm = format_compact(result.tpm)
    return {
        "Person": (result.owner_tag or "").strip(),
        "Email": (result.email or "").strip(),
        "Endpoint": endpoint,
        "TPM": tpm,
        "Proxy_Name": (result.new_api_name or "").strip(),
        "Pool": format_compact(result.credits_limit),
    }


def _upsert_rows(results: list[KimiDeployResult]) -> int:
    pending_by_host: dict[str, dict[str, str]] = {}
    for item in results:
        if not item.ok:
            continue
        row = _inventory_row(item)
        if not row:
            continue
        host = resource_key(row["Endpoint"])
        if not host:
            continue
        pending_by_host[host] = row
    pending = list(pending_by_host.values())
    if not pending:
        return 0
    with _lock:
        worksheet = _open_worksheet()
        mapping = _ensure_headers(worksheet)
        existing = worksheet.get_all_values()
        data_rows = existing[1:] if existing else []
        by_host: dict[str, int] = {}
        max_sno = 0
        for offset, row in enumerate(data_rows):
            sno_idx = mapping.get("Sno", 0)
            end_idx = mapping.get("Endpoint", 2)
            sno = _parse_sno(row[sno_idx] if sno_idx < len(row) else "")
            max_sno = max(max_sno, sno)
            host = resource_key(row[end_idx] if end_idx < len(row) else "")
            if host and host not in by_host:
                by_host[host] = offset
        writes: list[tuple[int, list[str]]] = []
        appends: list[list[str]] = []
        for payload in pending:
            host = resource_key(payload["Endpoint"])
            if host in by_host:
                offset = by_host[host]
                current = list(data_rows[offset])
                width = max(mapping.values()) + 1
                if len(current) < width:
                    current.extend([""] * (width - len(current)))
                sno = _cell(current, mapping, "Sno") or str(offset + 1)
                merged = {
                    "Sno": sno,
                    "Person": payload.get("Person") or _cell(current, mapping, "Person"),
                    "Email": payload["Email"] or _cell(current, mapping, "Email"),
                    "Endpoint": payload["Endpoint"] or _cell(current, mapping, "Endpoint"),
                    "TPM": payload["TPM"] or _cell(current, mapping, "TPM"),
                    "Proxy_Name": payload["Proxy_Name"] or _cell(current, mapping, "Proxy_Name"),
                    "Pool": payload["Pool"] or _cell(current, mapping, "Pool"),
                }
                if "API_Key" in mapping and not _cell(current, mapping, "API_Key"):
                    merged["API_Key"] = "-"
                writes.append((offset + 2, _row_values(mapping, merged, current)))
            else:
                max_sno += 1
                if not payload["TPM"]:
                    payload = {**payload, "TPM": "500k"}
                extra = {"Sno": str(max_sno)}
                if "API_Key" in mapping:
                    extra["API_Key"] = "-"
                appends.append(_row_values(mapping, {**payload, **extra}))
        for row_number, values in writes:
            end_col = chr(ord("A") + len(values) - 1)
            worksheet.update(f"A{row_number}:{end_col}{row_number}", [values], value_input_option="RAW")
        if appends:
            worksheet.append_rows(appends, value_input_option="RAW")
        return len(writes) + len(appends)


class SheetSyncError(Exception):
    pass


def _share_hint() -> str:
    try:
        email = str(_credentials_info().get("client_email") or "").strip()
    except Exception:
        email = ""
    if email:
        return f"Share the spreadsheet with {email} as Editor."
    return "Share the spreadsheet with the service account as Editor."


def _force_ok(results: list[KimiDeployResult]) -> list[KimiDeployResult]:
    out: list[KimiDeployResult] = []
    for item in results:
        row = item.model_copy()
        row.ok = True
        out.append(row)
    return out


async def push_inventory(results: list[KimiDeployResult]) -> int:
    """Manual sync. Raises if Sheets is unset or the write fails."""
    if not results:
        raise SheetSyncError("Nothing to sync.")
    if not configured():
        raise SheetSyncError(
            "Google Sheet is not configured. Set GOOGLE_SHEETS_SPREADSHEET_ID and a service-account key."
        )
    try:
        changed = await asyncio.to_thread(_upsert_rows, _force_ok(results))
    except SheetSyncError:
        raise
    except PermissionError as exc:
        raise SheetSyncError(_share_hint()) from exc
    except Exception as exc:
        message = str(exc)
        if "403" in message or "does not have permission" in message.lower():
            raise SheetSyncError(_share_hint()) from exc
        logger.exception("Could not update the Google Sheet inventory")
        raise SheetSyncError(message[:300]) from exc
    if changed == 0:
        raise SheetSyncError("Need an Azure endpoint on this card to sync.")
    return changed


async def sync_deploy_results(results: list[KimiDeployResult]) -> None:
    if not results or not configured():
        return
    try:
        changed = await asyncio.to_thread(_upsert_rows, results)
        if changed:
            logger.info("Google Sheet inventory updated for %s row(s)", changed)
    except Exception:
        logger.exception("Could not update the Google Sheet inventory")
