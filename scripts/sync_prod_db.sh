#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi

HOST="${PROD_SSH_HOST:-10.223.68.120}"
USER="${PROD_SSH_USER:-cre}"
WSL_PROJECT="${PROD_WSL_PROJECT:-/home/psl_3/mass_monitoring}"
PASS="${PROD_SSH_PASSWORD:-}"
PGUSER="${POSTGRES_USER:-portal}"
PGDB="${POSTGRES_DB:-llm_portal}"
SIDE_DB=""

usage() { echo "Usage: $0 pull|push [--dry-run] [--force source|dest]" >&2; }

CMD="${1:-}"
DRY_RUN=0
FORCE=""
shift || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --force)
      shift
      case "${1:-}" in
        source|s) FORCE=s ;;
        dest|d) FORCE=d ;;
        *) usage; exit 1 ;;
      esac
      ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 1 ;;
  esac
  shift
done
[[ "$CMD" == "pull" || "$CMD" == "push" ]] || { usage; exit 1; }
[[ -n "$PASS" ]] || { echo "Set PROD_SSH_PASSWORD in .env" >&2; exit 1; }
command -v sshpass >/dev/null || { echo "install sshpass" >&2; exit 1; }
docker compose ps db --status running >/dev/null 2>&1 || { echo "docker compose up -d db" >&2; exit 1; }

if [[ "$CMD" == "pull" ]]; then
  SIDE_DB=llm_portal_src
else
  SIDE_DB=llm_portal_dst
fi

SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o PreferredAuthentications=password -o PubkeyAuthentication=no)
ssh_run() { sshpass -p "$PASS" ssh "${SSH_OPTS[@]}" "$USER@$HOST" "$@"; }
remote_bash() { ssh_run wsl bash -s; }

dump_prod_to() {
  echo "==> dump prod (read only)"
  remote_bash <<EOF
set -euo pipefail
cd $(printf '%q' "$WSL_PROJECT")
docker compose exec -T db pg_dump -U portal -d llm_portal --no-owner --no-acl -Fc < /dev/null > /tmp/llm_portal.dump
EOF
  ssh_run wsl cat /tmp/llm_portal.dump </dev/null >"$1"
  ssh_run wsl rm -f /tmp/llm_portal.dump </dev/null || true
}

is_pg_dump() { file "$1" | grep -q "PostgreSQL custom database dump"; }

restore_temp_local() {
  echo "==> load snapshot into temp db $SIDE_DB on this Mac"
  docker compose cp "$1" db:/tmp/sync_src.dump
  docker compose exec -T db psql -U "$PGUSER" -d postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS $SIDE_DB WITH (FORCE);"
  docker compose exec -T db psql -U "$PGUSER" -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE $SIDE_DB OWNER $PGUSER;"
  set +e
  docker compose exec -T db pg_restore --no-owner --no-acl -U "$PGUSER" -d "$SIDE_DB" /tmp/sync_src.dump
  local rc=$?
  set -e
  docker compose exec -T db rm -f /tmp/sync_src.dump
  [[ "$rc" -le 1 ]]
}

apply_sql_prod() {
  echo "==> apply inserts/updates on prod"
  remote_bash <<EOF
set -euo pipefail
cd $(printf '%q' "$WSL_PROJECT")
base64 -d > /tmp/sync_apply.sql <<'END_MASS_MONITORING_SQL'
$(base64 < "$1")
END_MASS_MONITORING_SQL
test -s /tmp/sync_apply.sql
docker compose stop backend < /dev/null
set +e
docker compose exec -T db psql -U portal -d llm_portal -v ON_ERROR_STOP=1 < /tmp/sync_apply.sql
rc=\$?
set -e
docker compose start backend < /dev/null
rm -f /tmp/sync_apply.sql
exit \$rc
EOF
}

WORK="$(mktemp -d "${TMPDIR:-/tmp}/mass-monitoring-db-XXXXXX")"
cleanup() {
  if [[ -n "$SIDE_DB" ]]; then
    echo "==> drop temp db $SIDE_DB on this Mac"
    docker compose exec -T db psql -U "$PGUSER" -d postgres -c "DROP DATABASE IF EXISTS $SIDE_DB WITH (FORCE);" >/dev/null || true
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT

MERGE=(python3 -u "$ROOT/scripts/sync_merge.py")
[[ "$DRY_RUN" -eq 1 ]] && MERGE+=(--dry-run)
[[ -n "$FORCE" ]] && MERGE+=(--force "$FORCE")

if [[ "$CMD" == "pull" ]]; then
  dump_prod_to "$WORK/prod.dump"
  [[ -s "$WORK/prod.dump" ]] && is_pg_dump "$WORK/prod.dump" || { echo "bad prod dump" >&2; exit 1; }
  restore_temp_local "$WORK/prod.dump"
  "${MERGE[@]}" --src-db "$SIDE_DB" --dst-db "$PGDB" --src-label prod --dst-label local --apply-dst
else
  dump_prod_to "$WORK/prod.dump"
  [[ -s "$WORK/prod.dump" ]] && is_pg_dump "$WORK/prod.dump" || { echo "bad prod dump" >&2; exit 1; }
  restore_temp_local "$WORK/prod.dump"
  "${MERGE[@]}" --src-db "$PGDB" --dst-db "$SIDE_DB" --src-label local --dst-label prod --sql-out "$WORK/apply.sql"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    exit 0
  fi
  if [[ ! -s "$WORK/apply.sql" ]]; then
    echo "nothing to write to prod"
    exit 0
  fi
  printf "write inserts/updates to prod? [y/N] "
  read -r answer
  [[ "$answer" == "y" || "$answer" == "Y" ]] || { echo "aborted"; exit 1; }
  apply_sql_prod "$WORK/apply.sql"
  echo "done"
fi
