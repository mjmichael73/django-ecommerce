# Database Migrations Strategy

This document defines how schema changes are authored, reviewed, and deployed in
this Django e-commerce project. It is the team's reference for making and
shipping database changes alongside application code.

## 1. Zero-Downtime Contract: Expand → Migrate → Contract

Deployments must not block user traffic or corrupt data. Every schema change
follows the three-phase expand/migrate/contract model, and **each phase ships in
a separate release** so that two code generations can coexist during a rolling
restart.

### Phase A — Expand (backward-compatible)

Add new schema that the **current** code ignores. The old code must keep working
against the new schema. Typical operations:

- Add a **nullable** column or a column with a database-level default.
- Add a **new table** or a **new index**.
- Add a brand-new field to a model with `null=True` / `blank=True` plus a
  sensible Django-level default, so existing rows are unaffected.

Worked example (Django terms):

```python
# 0012_productvariant_sku.py
class Migration(migrations.Migration):
    dependencies = [("shop", "0011_...")]
    operations = [
        migrations.AddField(
            model_name="productvariant",
            name="sku",
            field=models.CharField(max_length=64, null=True, blank=True),
        ),
    ]
```

At this point the running image still reads/writes the **old** columns; the new
nullable `sku` column sits empty.

### Phase B — Migrate (backfill)

Populate the new structure from the old. This is a **data migration or a
one-off script**, never mixed into the schema migration above:

```bash
docker compose run --rm web python manage.py migrate
# then, in a release window or background job:
python manage.py shell < scripts/backfill_skus.py
```

Key rules:

- **Data migrations and schema migrations are separate steps.** Split them so
  each is individually reversible if needed.
- Prefer a reversible data migration; if a one-off script is unavoidable, keep
  it idempotent (safe to re-run).
- Do not block request latency on a large backfill — batch it, or run it as a
  management command invoked from a Celery task.

### Phase C — Contract (write new, drop old)

Only after the backfill is complete **and** the old code path has been removed:

- Switch application code to write/read the **new** column exclusively.
- Ship that code in a release and let it roll fully.
- **Last** step: drop the now-unused column or table in its own migration.

Why last? If you drop a column before all writers have migrated, a stray write
from an old pod crashes or silently truncates. The column is dead weight for one
release, then it is removed.

## 2. Django Rules

- **Never edit a released migration.** Once a migration has shipped to any
  environment, treat it as immutable. If you need to fix it, add a *new*
  migration on top.
- **`makemigrations --check --dry-run` is the CI gate.** It must exit `0` on the
  main branch. A non-zero exit means a model changed without a migration — the
  build fails and the author adds the missing migration. Beyond diffing model
  state against the migration graph, `--check` also consults the `django_migrations`
  history for consistency (`check_consistent_history`); if the database is
  unreachable it emits a `WARNING` rather than a hard failure, so the CI step
  still gates model-state drift without needing a live database connection.
- **Schema, data, and backfill are separate steps.** Each migration file should
  do one thing: add/drop a column, OR backfill data, OR change a constraint.
  Keeping them separate makes each step reversible on its own.
- **Avoid one-shot squashes.** Squashing rewrites history and complicates
  rollback reasoning. We do not squash migrations in this project.

## 3. Multi-Step Rollout Under the Shared Postgres

The application uses the **shared Postgres instance** from
`~/projects/shared-services` (external network `shared-services-network`,
database `ecommerce`). Two code generations coexist during a rolling restart:

- The **old image must tolerate the new schema** (expand-first principle).
  Because we only ever *add* nullable columns and *add* new tables/indexes in the
  expand phase, the old image runs unchanged against the widened schema.
- **Boot migrate runs before traffic.** `docker/entrypoint.sh` executes
  `python manage.py migrate --noinput` before the web process (Gunicorn) or the
  Celery worker accepts work. This is what makes expand-first safe: the schema
  is widened by the time the new image takes traffic, and the old image (already
  running) only sees additive changes.

Sequencing per deploy:

1. Author migration(s) + application code for the **expand** phase.
2. Merge + let CI verify `makemigrations --check`.
3. Deploy the new image. `entrypoint.sh` runs `migrate` at boot; the new
   nullable column/index lands. Old pods keep serving against the widened
   schema.
4. (Later release) switch the application to write the new column.
5. (Final release) drop the old column.

## 4. Entrypoint Boot-Migrate Behavior (Per Service)

`docker/entrypoint.sh` is the entrypoint for both the `web` and
`celery-worker` services (the `flower` service bypasses it entirely — see
`docker-compose.yml`).

### What the entrypoint does (literally, from the file)

```sh
# 1. If POSTGRES_HOST is set, wait until the TCP port is reachable
if [ -n "${POSTGRES_HOST:-}" ]; then
  until python -c "socket connect to host:port"; do
    echo "Waiting for PostgreSQL..."
    sleep 1
  done
fi

# 2. Apply migrations unconditionally (every boot)
python manage.py migrate --noinput

# 3. Collect static, unless the worker says to skip it
if [ "${SKIP_COLLECTSTATIC:-0}" != "1" ]; then
  python manage.py collectstatic --noinput
fi

# 4. Hand off to the CMD
exec "$@"
```

### Per-service behavior

| Service        | `POSTGRES_HOST` | `SKIP_COLLECTSTATIC` | Boots migrate? | Boots collectstatic? | CMD / purpose                          |
|----------------|-----------------|----------------------|----------------|----------------------|----------------------------------------|
| `web`          | `postgres`      | unset → `0`          | **Yes**        | **Yes**              | `gunicorn ... ecommerce.wsgi:application` |
| `celery-worker`| `postgres`      | `1`                  | **Yes**        | **No**               | `celery -A ecommerce worker -l info`   |
| `flower`       | `postgres`      | n/a (no entrypoint)  | No             | No                   | `celery -A ecommerce flower`           |

### Why it is safe for workers to also run `migrate`

- Both `web` and `celery-worker` start in dependency order (`celery-worker`
  `depends_on: web: condition: service_started`), so the web container's boot
  migrate lands first.
- `migrate` is idempotent by design: it consults `django_migrations` for the set
  of already-applied migrations and applies only those recorded absent — a worker
  that starts a few seconds after `web` simply sees `no-migrations-to-apply` and
  exits cleanly (a fast no-op).
- Each migration runs inside an atomic transaction (where the backend supports
  it), so a half-applied migration cannot persist: either the transaction
  commits the row in `django_migrations` together with the schema change, or it
  rolls back entirely.
- Container start-up ordering (`depends_on` in `docker-compose.yml`) plus
  Postgres's own DDL locking serializes conflicting `ALTER TABLE` statements,
  keeping concurrent schema writes from colliding.
- The residual race window that remains is the near-simultaneous boot case — two
  containers clearing the DB-wait loop at the same instant — where both could
  attempt the same migration. This is acceptable for this stack (a single schema
  owner, low replica count, and migrations that are additive and idempotent), and
  the transactional + DDL-locking behavior above bounds the failure mode to a
  harmless "already exists" or transaction rollback rather than corruption. For
  higher-assurance scenarios, add the `MIGRATE_DISABLE`-style knob documented
  below and run migrations from a single dedicated, human-gated deploy step.

### When a `MIGRATE_DISABLE`-style knob would be preferable

The shared-boot-migrate pattern is sufficient for **this stack** (single
project, one schema owner, no sidecar containers that lack migration
permissions). A deliberate disable knob becomes worthwhile when:

- Multiple containers in the same image should **not** all mutate the schema
  (e.g. read-only replicas, cron-only sidecars).
- Migrations must be performed by a dedicated, human-gated deploy step rather
  than implicitly at container start.
- The container role cannot be trusted with `ALTER TABLE` (least-privilege DB
  users per service).

We document the need but **do not implement** a `MIGRATE_DISABLE` flag on this
card. If either condition arises, add an env check in `entrypoint.sh`
(e.g. `if [ "${MIGRATE_DISABLE:-0}" != "1" ]; then python manage.py migrate --noinput; fi`)
and document the new deploy step.

## 5. Rollback Policy

- **Forward-fix is preferred for schema.** Django migrations describe a
  direction; reversing a column drop or a `RunSQL` data migration is fragile
  and often lossy. If a deploy is bad, prefer `ALTER` / a compensating
  migration that restores the prior column as nullable, then forward-fix.
- **When a revert is truly needed:** restore from the Postgres logical backup
  (see `docs/production.md` → *Backup & Restore*) to a **known-good point**,
  then replay migrations forward to the desired state. Record the restore point
  and ordering before you begin.
- **Out of scope:** no automated rollback tooling is introduced by this card.
  Rollbacks remain a manual, ops-driven procedure.

## 6. How to Make a Migration (Checklist)

1. Change the Django model(s) in the relevant app.
2. Inspect the diff:
   ```bash
   docker compose run --rm web python manage.py makemigrations --dry-run
   ```
3. Author the migration:
   ```bash
   docker compose run --rm web python manage.py makemigrations
   ```
4. Apply locally / via compose:
   ```bash
   make migrate
   ```
5. Commit the migration file alongside the model change. CI will fail the next
   step if a model change is missing its migration.
