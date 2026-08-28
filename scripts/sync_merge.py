#!/usr/bin/env python3
from __future__ import annotations

import csv
import io
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)
ROOT = Path(__file__).resolve().parent.parent
NAME_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
IGNORE = {
    "created_at",
    "updated_at",
    "last_synced_at",
    "last_sync_status",
    "last_sync_error",
    "credits_remaining",
    "credits_used",
    "credits_available",
    "new_api_used_quota",
    "new_api_cost_o1_usd",
    "new_api_cost_o2_usd",
    "new_api_cost_usd",
    "new_api_status",
    "new_api_status_o1",
    "new_api_status_o2",
    "new_api_synced_at",
}
NULL = r"\N"


@dataclass(frozen=True)
class Spec:
    table: str
    keys: tuple[str, ...]
    nk_fks: tuple[tuple[str, str], ...]
    fks: tuple[tuple[str, str], ...]


SPECS = [
    Spec("admin_accounts", ("username",), (), ()),
    Spec("app_settings", ("key",), (), ()),
    Spec("registered_models", ("name",), (), ()),
    Spec("provider_accounts", ("name",), (), ()),
    Spec("azure_service_principals", ("subscription_id",), (), ()),
    Spec("azure_openai_keys", ("subscription_id", "resource_name"), (), ()),
    Spec("sp_submit_requests", ("session_id",), (), ()),
    Spec("account_groups", ("name",), (), ()),
    Spec(
        "account_group_members",
        (),
        (("account_group_id", "account_groups"), ("provider_account_id", "provider_accounts")),
        (("account_group_id", "account_groups"), ("provider_account_id", "provider_accounts")),
    ),
    Spec(
        "monitored_models",
        ("deployment_name",),
        (("provider_account_id", "provider_accounts"),),
        (("provider_account_id", "provider_accounts"), ("registered_model_id", "registered_models")),
    ),
    Spec(
        "usage_snapshots",
        ("bucket_start",),
        (("monitored_model_id", "monitored_models"),),
        (("monitored_model_id", "monitored_models"), ("provider_account_id", "provider_accounts")),
    ),
    Spec(
        "cost_snapshots",
        ("usage_date",),
        (("provider_account_id", "provider_accounts"),),
        (("provider_account_id", "provider_accounts"),),
    ),
]


def die(msg: str, code: int = 1) -> None:
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def check_name(name: str) -> str:
    if not NAME_RE.match(name):
        die(f"bad name: {name}")
    return name


class LocalPg:
    def __init__(self) -> None:
        self.pg_user = os.environ.get("POSTGRES_USER", "portal")

    def _run(self, db: str, sql: str, tuples: bool = True) -> str:
        args = [
            "docker",
            "compose",
            "exec",
            "-T",
            "db",
            "psql",
            "-U",
            self.pg_user,
            "-d",
            check_name(db),
            "-v",
            "ON_ERROR_STOP=1",
            "-q",
        ]
        if tuples:
            args += ["-At"]
        r = subprocess.run(args, cwd=ROOT, input=sql, capture_output=True, text=True)
        if r.returncode != 0:
            die((r.stderr or r.stdout or f"psql failed ({r.returncode})").strip())
        return r.stdout

    def compose(self, args: list[str]) -> None:
        r = subprocess.run(["docker", "compose", *args], cwd=ROOT, capture_output=True, text=True)
        if r.returncode != 0:
            die((r.stderr or r.stdout or "docker compose failed").strip())

    def tables(self, db: str) -> set[str]:
        out = self._run(
            db,
            "SELECT relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relkind='r';",
        )
        return {line.strip() for line in out.splitlines() if line.strip()}

    def columns(self, db: str, table: str) -> list[str]:
        out = self._run(
            db,
            "SELECT column_name FROM information_schema.columns "
            f"WHERE table_schema='public' AND table_name='{check_name(table)}' "
            "ORDER BY ordinal_position;",
        )
        return [line.strip() for line in out.splitlines() if line.strip()]

    def copy_out(self, db: str, table: str) -> list[dict[str, str]]:
        raw = self._run(
            db,
            f"COPY public.{check_name(table)} TO STDOUT WITH (FORMAT csv, HEADER true, NULL '{NULL}');",
        )
        if not raw.strip():
            return []
        return list(csv.DictReader(io.StringIO(raw)))


def cell(row: dict[str, str], col: str) -> str:
    val = row.get(col, NULL)
    return NULL if val is None else val


def nk(spec: Spec, row: dict[str, str], by_id: dict[str, dict[str, dict[str, str]]]) -> tuple[str, ...] | None:
    parts: list[str] = []
    for col, parent in spec.nk_fks:
        parent_row = by_id.get(parent, {}).get(cell(row, col))
        if parent_row is None:
            return None
        parent_spec = next(s for s in SPECS if s.table == parent)
        parent_nk = nk(parent_spec, parent_row, by_id)
        if parent_nk is None:
            return None
        parts.extend(parent_nk)
    for col in spec.keys:
        parts.append(cell(row, col))
    return tuple(parts)


def index_ids(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {cell(r, "id"): r for r in rows if "id" in r and cell(r, "id") != NULL}


def data_cols(spec: Spec, cols: list[str]) -> list[str]:
    skip = IGNORE | {"id"} | {c for c, _ in spec.fks}
    return [c for c in cols if c not in skip]


def diffs(spec: Spec, cols: list[str], src: dict[str, str], dst: dict[str, str]) -> list[tuple[str, str, str]]:
    out = []
    for col in data_cols(spec, cols):
        a, b = cell(src, col), cell(dst, col)
        if a != b:
            out.append((col, a, b))
    return out


def lit(val: str) -> str:
    if val == NULL:
        return "NULL"
    return "'" + val.replace("'", "''") + "'"


def fmt_nk(key: tuple[str, ...]) -> str:
    return ",".join(key)


def prompt_conflict(table: str, label: str, src_label: str, dst_label: str, changed: list[tuple[str, str, str]]) -> str:
    print(f"\nconflict {table}  {label}")
    for col, src_val, dst_val in changed[:12]:
        print(f"  {col}: {src_label}={src_val}  {dst_label}={dst_val}")
    if len(changed) > 12:
        print(f"  … {len(changed) - 12} more columns")
    while True:
        choice = input(
            f"  [s] {src_label}  [d] {dst_label}  [S] {src_label} rest of table  [D] {dst_label} rest of table: "
        ).strip()
        if choice in {"s", "d", "S", "D"}:
            return choice
        print("  type s, d, S, or D")


def remap(spec: Spec, row: dict[str, str], maps: dict[str, dict[str, str]]) -> dict[str, str] | None:
    out = dict(row)
    for col, parent in spec.fks:
        src_id = cell(row, col)
        if src_id == NULL:
            out[col] = NULL
            continue
        dest_id = maps.get(parent, {}).get(src_id)
        if dest_id is None:
            return None
        out[col] = dest_id
    return out


def csv_block(table: str, cols: list[str], rows: list[dict[str, str]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    for row in rows:
        writer.writerow([cell(row, c) for c in cols])
    header = ", ".join(cols)
    return f"COPY public.{table} ({header}) FROM STDIN WITH (FORMAT csv, NULL '{NULL}');\n{buf.getvalue()}\\.\n"


def build_sql(
    specs: list[Spec],
    cols: dict[str, list[str]],
    inserts: dict[str, list[dict[str, str]]],
    updates: dict[str, list[tuple[dict[str, str], dict[str, str]]]],
) -> str:
    parts = ["BEGIN;"]
    for spec in specs:
        table = spec.table
        for mapped, dst_row in updates.get(table, []):
            assigns = [f"{col}={lit(cell(mapped, col))}" for col in cols[table] if col != "id"]
            if not assigns:
                continue
            if "id" in dst_row:
                where = f"id={lit(cell(dst_row, 'id'))}"
            else:
                where = " AND ".join(f"{col}={lit(cell(dst_row, col))}" for col in spec.keys)
            parts.append(f"UPDATE public.{table} SET {', '.join(assigns)} WHERE {where};")
        rows = inserts.get(table, [])
        if rows:
            use_cols = [c for c in cols[table] if c in rows[0]]
            parts.append(csv_block(table, use_cols, rows))
            if "id" in use_cols:
                parts.append(
                    f"SELECT setval(pg_get_serial_sequence('public.{table}','id'), "
                    f"GREATEST(1, COALESCE((SELECT MAX(id) FROM public.{table}), 1)));"
                )
    parts.append("COMMIT;")
    return "\n".join(parts) + "\n"


def merge(src_db: str, dst_db: str, src_label: str, dst_label: str, dry: bool, force: str | None, sql_out: str, apply_dst: bool) -> int:
    pg = LocalPg()
    src_tables = pg.tables(src_db)
    dst_tables = pg.tables(dst_db)
    src_data: dict[str, list[dict[str, str]]] = {}
    dst_data: dict[str, list[dict[str, str]]] = {}
    cols: dict[str, list[str]] = {}
    print("==> compare on this Mac")
    for spec in SPECS:
        if spec.table not in src_tables or spec.table not in dst_tables:
            continue
        cols[spec.table] = pg.columns(dst_db, spec.table)
        src_data[spec.table] = pg.copy_out(src_db, spec.table)
        dst_data[spec.table] = pg.copy_out(dst_db, spec.table)
        print(f"    {spec.table}: {src_label} {len(src_data[spec.table])} rows, {dst_label} {len(dst_data[spec.table])} rows")

    src_by_id = {table: index_ids(rows) for table, rows in src_data.items()}
    dst_by_id = {table: index_ids(rows) for table, rows in dst_data.items()}
    maps: dict[str, dict[str, str]] = {spec.table: {} for spec in SPECS}
    inserts: dict[str, list[dict[str, str]]] = {}
    updates: dict[str, list[tuple[dict[str, str], dict[str, str]]]] = {}
    skipped = 0
    n_conflict = 0

    print("==> diff")
    for spec in SPECS:
        if spec.table not in src_data:
            continue
        src_rows, dst_rows = src_data[spec.table], dst_data[spec.table]
        dst_nk: dict[tuple[str, ...], dict[str, str]] = {}
        for row in dst_rows:
            key = nk(spec, row, dst_by_id)
            if key is not None:
                dst_nk[key] = row
        used_ids = {cell(r, "id") for r in dst_rows if "id" in r and cell(r, "id") != NULL}
        next_id = max((int(i) for i in used_ids), default=0) + 1
        table_policy: str | None = force
        new_rows: list[dict[str, str]] = []
        new_keys: list[tuple[str, ...]] = []
        conflict_rows: list[tuple[dict[str, str], dict[str, str], tuple[str, ...]]] = []
        same = 0
        for src_row in src_rows:
            key = nk(spec, src_row, src_by_id)
            if key is None:
                skipped += 1
                continue
            dst_row = dst_nk.get(key)
            if dst_row is None:
                mapped = remap(spec, src_row, maps)
                if mapped is None:
                    skipped += 1
                    continue
                if "id" in mapped:
                    src_id = cell(src_row, "id")
                    if src_id not in used_ids:
                        dest_id = src_id
                    else:
                        dest_id = str(next_id)
                        next_id += 1
                    used_ids.add(dest_id)
                    mapped["id"] = dest_id
                    maps[spec.table][src_id] = dest_id
                new_rows.append(mapped)
                new_keys.append(key)
                continue
            if "id" in src_row and "id" in dst_row:
                maps[spec.table][cell(src_row, "id")] = cell(dst_row, "id")
            changed = diffs(spec, cols[spec.table], src_row, dst_row)
            if not changed:
                same += 1
                continue
            conflict_rows.append((src_row, dst_row, key))

        chosen: list[tuple[dict[str, str], dict[str, str]]] = []
        if not dry:
            for src_row, dst_row, key in conflict_rows:
                changed = diffs(spec, cols[spec.table], src_row, dst_row)
                pick = table_policy
                if pick is None:
                    if not sys.stdin.isatty():
                        die("conflicts need a tty, or pass --force source|dest")
                    pick = prompt_conflict(spec.table, fmt_nk(key), src_label, dst_label, changed)
                    if pick == "S":
                        table_policy = "s"
                        pick = "s"
                    elif pick == "D":
                        table_policy = "d"
                        pick = "d"
                if pick == "s":
                    mapped = remap(spec, src_row, maps)
                    if mapped is None:
                        skipped += 1
                        continue
                    if "id" in dst_row:
                        mapped["id"] = cell(dst_row, "id")
                    chosen.append((mapped, dst_row))
        inserts[spec.table] = new_rows
        updates[spec.table] = chosen
        n_conflict += len(conflict_rows)
        dest_only = len(dst_rows) - same - len(conflict_rows)
        print(
            f"    {spec.table}: new {len(new_rows)}  conflict {len(conflict_rows)}  "
            f"same {same}  {dst_label}-only {max(dest_only, 0)}"
        )
        for key in new_keys[:8]:
            print(f"      + {fmt_nk(key)}")
        if len(new_keys) > 8:
            print(f"      + … {len(new_keys) - 8} more")
        for src_row, dst_row, key in conflict_rows[:8]:
            changed = diffs(spec, cols[spec.table], src_row, dst_row)
            print(f"      ! {fmt_nk(key)}  {', '.join(c[0] for c in changed[:6])}")
        if len(conflict_rows) > 8:
            print(f"      ! … {len(conflict_rows) - 8} more")

    n_ins = sum(len(v) for v in inserts.values())
    n_upd = sum(len(v) for v in updates.values())
    if skipped:
        print(f"skipped {skipped} rows (missing parent)")
    print(f"summary: {n_ins} insert(s), {n_conflict} conflict(s), {n_upd} update(s) chosen")
    if dry:
        return 0
    if n_ins == 0 and n_upd == 0:
        print("nothing to write")
        return 0
    sql = build_sql(SPECS, cols, inserts, updates)
    if sql_out:
        Path(sql_out).write_text(sql)
        print(f"wrote {sql_out}")
    if apply_dst:
        print(f"==> write to local {dst_db}")
        pg.compose(["stop", "backend"])
        try:
            pg._run(dst_db, sql, tuples=False)
        finally:
            pg.compose(["start", "backend"])
        print("done")
    return 0


def main() -> int:
    args = sys.argv[1:]
    src_db = dst_db = src_label = dst_label = sql_out = ""
    dry = False
    apply_dst = False
    force: str | None = None
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--src-db":
            src_db = args[i + 1]
            i += 2
        elif arg == "--dst-db":
            dst_db = args[i + 1]
            i += 2
        elif arg == "--src-label":
            src_label = args[i + 1]
            i += 2
        elif arg == "--dst-label":
            dst_label = args[i + 1]
            i += 2
        elif arg == "--sql-out":
            sql_out = args[i + 1]
            i += 2
        elif arg == "--dry-run":
            dry = True
            i += 1
        elif arg == "--apply-dst":
            apply_dst = True
            i += 1
        elif arg == "--force":
            force = args[i + 1]
            i += 2
        else:
            die(f"unknown arg {arg}")
    if not all([src_db, dst_db, src_label, dst_label]):
        die("sync_merge.py --src-db D --dst-db D --src-label S --dst-label D")
    if force not in {None, "s", "d"}:
        die("--force s|d")
    return merge(src_db, dst_db, src_label, dst_label, dry, force, sql_out, apply_dst)


if __name__ == "__main__":
    raise SystemExit(main())
