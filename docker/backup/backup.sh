#!/bin/sh
# One-shot backup of the shared Postgres 'ecommerce' database + the media_data
# volume, with retention rotation. The retention math lives in retain.py
# (pure-function, stdlib only); this shell script owns the filesystem side.
#
# Runs as a Compose one-shot (profile: backup) using the default image
#   ${BACKUP_CLIENT_IMAGE:-postgres:18-alpine}
# which ships pg_dump. The app image (web) has no pg_dump and is never used here.
#
# Env (see docker-compose.yml 'backup' service):
#   POSTGRES_DB / POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_HOST[:port]
#   BACKUP_KEEP_N  (default 14)
#   BACKUP_REDIS   (set != empty to also dump Redis via the redis-backup service)
#   DRY_RUN        (legacy alias; prefer --dry-run on the command line)
set -eu

BACKUP_DIR="${BACKUP_DIR:-/backups}"
KEEP_N="${BACKUP_KEEP_N:-14}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"

DRY_RUN=0
# Explicit arg check (avoids fragile grep-over-pipe on $* under `set -u`).
for _arg in "$@"; do
    case "$_arg" in
        --dry-run) DRY_RUN=1 ;;
    esac
done
# Legacy env alias.
case "${DRY_RUN:-}" in
    1|true|yes) DRY_RUN=1 ;;
esac
unset _arg

# libpq/pg_dump read PGPASSWORD; POSTGRES_PASSWORD is what Compose passes.
export PGPASSWORD="${POSTGRES_PASSWORD:-}"
export PGUSER="${POSTGRES_USER:-postgres}"
export PGDATABASE="${POSTGRES_DB:-ecommerce}"

# Resolve host/port from POSTGRES_HOST (may be "host:port").
PGHOST_PART="${POSTGRES_HOST%%:*}"
if [ "$PGHOST_PART" = "$POSTGRES_HOST" ]; then
    export PGHOST="$PGHOST_PART"
    export PGPORT="${PGPORT:-5432}"
else
    export PGHOST="$PGHOST_PART"
    export PGPORT="${POSTGRES_HOST##*:}"
fi

# Retention: delegate to retain.py when python3 is available (preferred), else
# fall back to a POSIX shell date-sort so the script still rotates on the bare
# postgres:18-alpine image. Either way the shell performs the unlinking.
_has_python3() { command -v python3 >/dev/null 2>&1; }

# list_files: glob a kind pattern into real files (skips literal unmatched globs).
_list_files() {
    _found=0
    for f in "$@"; do
        [ -e "$f" ] && { printf '%s\n' "$f"; _found=1; }
    done
    [ "$_found" -eq 0 ] && return 1 || return 0
}

# Sort matching files newest-first by the embedded 14-digit timestamp, print the
# delete set (all but the first KEEP_N). Uses retain.py when python3 is present.
_rotate_kind() {
    kind="$1"; shift
    if ! _list_files "$@"; then
        echo "[backup] (no existing ${kind} backups to rotate)"
        return 0
    fi
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "[backup][dry-run] rotate ${kind} (keep_n=$KEEP_N):"
    fi
    if _has_python3; then
        if [ "$DRY_RUN" -eq 1 ]; then
            # retain.py dry-run plan to stderr.
            python3 /backup/retain.py "$@" --keep-n "$KEEP_N" --dry-run --kind "$kind" || true
        else
            # retain.py emits the KEEP list to stdout (one per line); delete
            # everything that is NOT in that keep list (line-exact match).
            keep_list="$(python3 /backup/retain.py "$@" --keep-n "$KEEP_N" --kind "$kind" 2>/dev/null || true)"
            for f in "$@"; do
                [ -e "$f" ] || continue
                if printf '%s\n' "$keep_list" | grep -qxF -- "$f"; then
                    :
                else
                    rm -f -- "$f" && echo "[backup] rotated out: $f"
                fi
            done
        fi
    else
        # Shell fallback: extract 14-digit ts from each filename, sort desc,
        # drop all but the newest KEEP_N.
        _shell_fallback "$kind" "$@"
    fi
}

_shell_fallback() {
    kind="[$1]"; shift
    # Build "ts<space>file" lines, sort numeric desc, keep KEEP_N, delete rest.
    list=""
    for f in "$@"; do
        [ -e "$f" ] || continue
        # Extract the 14-digit UTC ts (YYYYmmddTHHMMSSZ -> drop the 'T' and 'Z').
        ts="$(printf '%s' "${f##*/}" | grep -oE '[0-9]{8}T[0-9]{6}' | tr -d 'T' || true)"
        [ -n "$ts" ] || ts="0"
        list="$list$ts $f
"
    done
    if [ -z "$list" ]; then
        return 0
    fi
    printf '%s' "$list" | sort -t' ' -k1,1nr \
        | tail -n +$((KEEP_N + 1)) \
        | while IFS= read -r line; do
            [ -z "$line" ] && continue
            # Drop the leading "ts " prefix (14-digit ts + space) to recover filename.
            f="$(printf '%s' "$line" | sed 's/^[0-9]* //')"
            if [ -n "$f" ]; then
                if [ "$DRY_RUN" -eq 1 ]; then
                    echo "[backup][dry-run] would delete: $f"
                else
                    rm -f -- "$f" && echo "[backup] rotated out: $f"
                fi
            fi
        done
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "[backup][dry-run] (shell fallback: no python3 in this image)"
    fi
}

mkdir -p "$BACKUP_DIR"

echo "[backup] start: ts=$TS kind=db host=$PGHOST port=$PGPORT db=${POSTGRES_DB}"

# --- Dry-run: print every planned action, write nothing ---------------------
if [ "$DRY_RUN" -eq 1 ]; then
    DB_DUMP="$BACKUP_DIR/db-${TS}.dump"
    MEDIA_TAR="$BACKUP_DIR/media-${TS}.tar.gz"
    echo "[backup][dry-run] would: pg_dump -Fc -d ${POSTGRES_DB} -f $DB_DUMP"
    echo "[backup][dry-run] would: tar -C /media -czf $MEDIA_TAR ."
    _rotate_kind db "$BACKUP_DIR"/db-*.dump
    _rotate_kind media "$BACKUP_DIR"/media-*.tar.gz
    if [ -n "${BACKUP_REDIS:-}" ]; then
        _rotate_kind redis "$BACKUP_DIR"/redis-*.rdb
    fi
    echo "[backup][dry-run] wrote nothing to disk"
    echo "[backup] done: ts=$TS (dry-run)"
    exit 0
fi

# --- Real run: dump + tar + rotate -----------------------------------------
DB_DUMP="$BACKUP_DIR/db-${TS}.dump"
MEDIA_TAR="$BACKUP_DIR/media-${TS}.tar.gz"

pg_dump -Fc -d "${POSTGRES_DB}" -f "$DB_DUMP"
echo "[backup] wrote $DB_DUMP"

# /media is the read-only bind of the media_data volume.
tar -C /media -czf "$MEDIA_TAR" .
echo "[backup] wrote $MEDIA_TAR"

_rotate_kind db "$BACKUP_DIR"/db-*.dump
_rotate_kind media "$BACKUP_DIR"/media-*.tar.gz
if [ -n "${BACKUP_REDIS:-}" ]; then
    _rotate_kind redis "$BACKUP_DIR"/redis-*.rdb
fi

echo "[backup] done: ts=$TS"
