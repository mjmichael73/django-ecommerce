# Backup & Restore — Django E-commerce

This project (the app code in `be/ecommerce/`) does **not** own persistent
Postgres or Redis data — those run as the shared instance in
`~/projects/shared-services` on the `shared-services-network`. That shared
instance already has its own scheduled `daily/` and `weekly/` backups (run from
that repo, outside this one).

This repository adds **one-shot operational backups** for this app's two pieces
of state: the `ecommerce` Postgres database and the `media_data` Docker volume
(product images, uploaded media). It also provides a safe **restore drill**
that never touches the live database.

## What is backed up

| Item | How | Where | Optional |
|------|-----|-------|----------|
| Postgres database (`ecommerce`) | `pg_dump -Fc` (custom-format archive) | `./backups/db-<UTC ts>.dump` | no |
| Media volume (`media_data`) | `tar -czf` of `/media` (read-only mount) | `./backups/media-<UTC ts>.tar.gz` | no |
| Redis (DB 1 results, DB 2 cache) | `redis-cli --rdb` (RDB snapshot) | `./backups/redis-<UTC ts>.rdb` | yes (`BACKUP_REDIS=1`) |

Redis data for this app is disposable by design (Celery results + rate-limit
cache), so the Redis dump is **off by default**. Enable with `BACKUP_REDIS=1`.

## Where files land

All artifacts are written to `./backups/` at the repository root, which is
**gitignored** (see `.gitignore`). They are written by the `backup` Compose
service (profile: `backup`, not started by default) using the official
`postgres:18-alpine` image, which ships `pg_dump` and `pg_restore`.

The app image (`web`) **has no pg_dump** and no bind-mount of the repo; the
backup service does **not** use the app image. No changes to the Dockerfile or
app image are made.

## The backup image

The backup client image defaults to **`postgres:18-alpine`**, matching the shared
Postgres 18 server major (a `pg_dump` client major must be >= the server major).
Override only if your shared instance differs:

```bash
BACKUP_CLIENT_IMAGE=postgres:18-alpine   # only override for a different major
```

## Usage

```bash
# Ensure the shared Postgres + Redis are running:
cd ~/projects/shared-services && docker compose up -d postgres redis

# Back up both the DB and media (writes to ./backups/):
make backup

# Dry run — prints every planned action, writes nothing:
make backup-dry-run

# Restore / verify a dump:
make backup-restore ARGS=backups/db-20240101T020000Z.dump

# Optional Redis backup (requires BACKUP_REDIS=1):
BACKUP_REDIS=1 make backup-redis
```

### Environment

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGRES_DB` | `ecommerce` | Database to dump |
| `POSTGRES_USER` | `ecommerce` | Postgres user |
| `POSTGRES_PASSWORD` | *(required)* | Required; no default |
| `POSTGRES_HOST` | `postgres:5432` | Host[:port] as seen by the backup client |
| `BACKUP_KEEP_N` | `14` | How many of each kind to keep |
| `BACKUP_REDIS` | *(unset)* | Set non-empty to enable Redis RDB dump |
| `BACKUP_CLIENT_IMAGE` | `postgres:18-alpine` | Override only for a different Postgres major |

## Retention

Retention rotation is delegated to `docker/backup/retain.py` (stdlib only,
pure function — `select_retention(entries, keep_n)` returning `(keep, delete)`
lists). The shell script owns the filesystem side. After each backup pass, the
oldest entries beyond `BACKUP_KEEP_N` (default **14**) of each kind (db, media,
redis) are unlinked.

`retain.py` also accepts `--dry-run` to print the keep/delete plan without
deleting anything.

## Dry run

```bash
make backup-dry-run
```

This runs `backup.sh --dry-run`, which prints every action it *would* take
(pg_dump, tar, rotation plan) and writes **nothing** to `./backups/`.

## Restore drill (scratch database)

The restore tool **never** touches the live `ecommerce` database. It operates
in two modes:

1. **Verify mode** (default) — `pg_restore -l` lists the archive contents,
   proving the dump is readable. Writes nothing.

2. **Scratch restore** (`CONFIRM=scratch`) — drops (only) the scratch database
   `ecommerce_restore_drill`, creates it, restores the dump into it, reports
   the table count, and drops the scratch db again (unless `KEEP_SCRATCH=1`).
   The scratch db is dropped/cleaned automatically; the drill never writes
   outside the scratch db.

```bash
# Verify a dump is readable:
make backup-restore ARGS=backups/db-20240101T020000Z.dump

# Restore into the scratch db (never the live db):
make backup-restore ARGS=backups/db-20240101T020000Z.dump CONFIRM=scratch

# Keep the scratch db afterwards:
make backup-restore ARGS=backups/db-20240101T020000Z.dump CONFIRM=scratch KEEP_SCRATCH=1
```

## Scheduling (production)

This repo provides one-shot backup tooling only. For automated scheduling in
production, use a cron line or CI schedule that invokes the profile-gated
services:

```cron
# Daily DB + media backup at 02:00 (keep 14 days)
0 2 * * * cd /path/to/django-ecommerce && docker compose --profile backup run --rm backup

# Optional daily Redis backup (keep 7 days)
0 3 * * * cd /path/to/django-ecommerce && BACKUP_REDIS=1 BACKUP_KEEP_N=7 docker compose --profile backup run --rm redis-backup
```

> **Note:** The shared Postgres instance in `~/projects/shared-services`
> already has its own scheduled daily/weekly backup-cron managed in that
> repository. This repo's one-shot backups complement — and do not duplicate —
> that scheduled backup layer.

## Files

| Path | Purpose |
|------|---------|
| `docker/backup/backup.sh` | One-shot DB + media backup with retention |
| `docker/backup/restore.sh` | Verify / scratch restore (never live db) |
| `docker/backup/retain.py` | Pure-function retention selector |
| `docker-compose.yml` (`backup` + `redis-backup` services) | Profile: `backup` wiring |
| `Makefile` (`backup`, `backup-restore`, `backup-dry-run`, `backup-redis`) | Host convenience targets |
