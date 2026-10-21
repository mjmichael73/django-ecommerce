#!/bin/sh
# Restore/verify tool for ecommerce backups.
#
# NEVER touches the live `ecommerce` database. Two modes:
#
#   default (no args / no confirmation):
#       pg_restore -l listing of the named dump, proving it is readable.
#
#   CONFIRM=scratch :
#       drops (only) the scratch database `ecommerce_restore_drill`,
#       creates it, restores the dump into it, and reports. The scratch db
#       is dropped again afterwards unless KEEP_SCRATCH=1.
#
# Env:
#   DUMP_PATH  (required)  the .dump file to restore/verify
#   CONFIRM    set to "scratch" to perform a real restore into the scratch db
#   KEEP_SCRATCH  (default 0) keep the scratch db after restore
#   POSTGRES_DB / POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_HOST[:port]
set -eu

DUMP_PATH="${DUMP_PATH:-${1:-}}"
CONFIRM="${CONFIRM:-}"
KEEP_SCRATCH="${KEEP_SCRATCH:-0}"

SCRATCH_DB="ecommerce_restore_drill"
LIVE_DB="${POSTGRES_DB:-ecommerce}"

usage() {
    cat >&2 <<EOF
usage: restore.sh DUMP_PATH            # verify (pg_restore -l listing)
       restore.sh DUMP_PATH CONFIRM=scratch  # restore into scratch db only
env:   DUMP_PATH=<file> CONFIRM=scratch KEEP_SCRATCH=1
EOF
    exit 2
}

[ -n "$DUMP_PATH" ] || usage
[ -f "$DUMP_PATH" ] || { echo "restore: dump not found: $DUMP_PATH" >&2; exit 1; }

# Resolve host/port from POSTGRES_HOST (may be "host:port").
PGHOST_PART="${POSTGRES_HOST%%:*}"
if [ "$PGHOST_PART" = "$POSTGRES_HOST" ]; then
    export PGHOST="$PGHOST_PART"
    export PGPORT="${PGPORT:-5432}"
else
    export PGHOST="$PGHOST_PART"
    export PGPORT="${POSTGRES_HOST##*:}"
fi
export PGUSER="${POSTGRES_USER:-postgres}"
export PGPASSWORD="${POSTGRES_PASSWORD:-}"
export PGDATABASE="${POSTGRES_DB:-ecommerce}"

# Hard guard: this script only ever targets SCRATCH_DB above (never LIVE_DB).
# The DROP DATABASE / pg_restore -d commands below all reference SCRATCH_DB or
# the literal maintenance db 'postgres' — never POSTGRES_DB / 'ecommerce'.
# See test_backup_procedures.RestoreScriptTextTests for the static guard.


echo "[restore] dump: $DUMP_PATH"
echo "[restore] host: $PGHOST port: $PGPORT (scratch db: $SCRATCH_DB, live db: $LIVE_DB)"

if [ "$CONFIRM" != "scratch" ]; then
    # --- Verify mode: list archive contents, write nothing ---
    echo "[restore] verify mode (no CONFIRM=scratch): listing archive only"
    pg_restore -l "$DUMP_PATH"
    echo "[restore] verify OK: archive is readable"
    exit 0
fi

# --- Scratch restore mode ----------------------------------------------
echo "[restore] CONFIRM=scratch: restoring into '$SCRATCH_DB' ONLY (never '$LIVE_DB')"

# Drop + recreate the scratch db. These DDL commands target SCRATCH_DB, never LIVE_DB.
psql -v ON_ERROR_STOP=1 -d postgres -h "$PGHOST" -p "$PGPORT" -U "${POSTGRES_USER}" <<SQL
DROP DATABASE IF EXISTS "${SCRATCH_DB}";
CREATE DATABASE "${SCRATCH_DB}";
SQL
echo "[restore] scratch db (re)created: $SCRATCH_DB"

# Restore the dump into the scratch db only.
PGDATABASE="$SCRATCH_DB" pg_restore --no-owner --no-acl -d "$SCRATCH_DB" "$DUMP_PATH"
echo "[restore] restore OK into $SCRATCH_DB"

# List table count as a sanity report (read-only, scratch db only).
TABLES="$(psql -d "$SCRATCH_DB" -h "$PGHOST" -p "$PGPORT" -U "${POSTGRES_USER}" -Atc \
    "select count(*) from information_schema.tables where table_schema='public'")"
echo "[restore] scratch db '$SCRATCH_DB' contains $TABLES user tables"

if [ "$KEEP_SCRATCH" != "1" ]; then
    psql -v ON_ERROR_STOP=1 -d postgres -h "$PGHOST" -p "$PGPORT" -U "${POSTGRES_USER}" <<SQL
DROP DATABASE IF EXISTS "${SCRATCH_DB}";
SQL
    echo "[restore] scratch db dropped: $SCRATCH_DB"
fi

echo "[restore] done"
