# Kubernetes Deployment — Django E-commerce

Card a9cea652 — Phase 5 deployment. This project ships Kustomize manifests under
`k8s/` (no Helm). They deploy the same container built by CI (GHCR) to a cluster
that supplies an **external** PostgreSQL and Redis/RabbitMQ — this project does
NOT run databases in-cluster.

## Layout

```
k8s/
├── base/
│   ├── configmap.yaml          # non-secret env (DEBUG, DB host/port, pool tunables, observability flags)
│   ├── secret.yaml             # PLACEHOLDER secret-class values only — never real credentials
│   ├── deployment-web.yaml     # gunicorn, port 8000, readiness /health/, liveness tcp
│   ├── deployment-worker.yaml  # celery worker (command override)
│   ├── deployment-beat.yaml    # celery beat (command override; no periodic tasks by default)
│   ├── service-web.yaml        # ClusterIP 80 -> 8000
│   └── kustomization.yaml      # base resources + image anchor (ghcr.io/mjmichael73/django-ecommerce:main)
├── overlays/dev/               # DEBUG=1, 1 replica, placeholder secrets
└── overlays/prod/              # DEBUG=0, 3 web replicas, tightened limits
```

**Base** holds the canonical, environment-neutral manifests. **Overlays** patch
replicas and the few env vars that vary per environment. Both overlays reference
`../base` via the Kustomize `bases` field, so the validator asserts each
overlay points at the base.

### Services in the base

| Resource | Purpose |
|----------|---------|
| `configmap.yaml` | Non-secret env consumed via `envFrom`. Mirrors `.env.example`. |
| `secret.yaml` | `stringData` placeholders only (`REPLACE-ME-...`). Real values come from your cluster's secret manager / sealed-secrets / External Secrets. |
| `deployment-web` | gunicorn via the image CMD. Readiness = `/health/`; liveness = `tcpSocket:8000`. |
| `deployment-worker` | `command: celery -A ecommerce worker -l info`. |
| `deployment-beat` | `command: celery -A ecommerce beat -l info`; **no periodic tasks configured by default** — it idles harmlessly. |
| `service-web` | ClusterIP 80 → 8000, selector matches the web pod labels (`app: ecommerce-web`). |

## External dependencies

This repo never ships in-cluster databases. The cluster provides:

- **PostgreSQL** (shared-services instance is version 18) — supply
  `POSTGRES_HOST`/`POSTGRES_PORT` via the ConfigMap/Secret; credentials via the
  Secret. Bring your own backup/restore — see [Backup & Restore](backup-restore.md)
  and the Task-6 procedure (`docker/backup/` scripts). A k8s CronJob that calls
  the same scripts is the intended future hook, but is **out of scope** for this
  task.
- **Redis** — result backend + rate-limit cache (DBs 1 and 2 respectively).
- **RabbitMQ** — Celery broker.

> Media storage: user uploads need a shared store across replicas. Mount a PVC
> (e.g. `emptyDir` placeholder in the base, replace with a real PVC in-cluster)
> or object storage. Pods serving `/media/` are not viable on a single-pod
> filesystem. This is noted here only; no implementation is added in this task.

## Readiness vs liveness split

There is exactly **one** health endpoint in this repo: **`/health/`**
(`ecommerce/metrics/views.py`, mounted in `ecommerce/urls.py`). It returns `200`
with JSON when `SELECT 1` succeeds and `503` otherwise. There is no `/healthz`.

That single endpoint touches the database, so we split its use across two
probes:

- **readiness** = `httpGet /health/ :8000`. Because it runs `SELECT 1`, it
  genuinely reflects DB reachability — the load balancer must stop routing a pod
  whose DB probe fails. `initialDelaySeconds` is short (15s) so a cold-starting
  pod isn't sent traffic before it's ready.
- **liveness** = `tcpSocket 8000` (non-DB). A transient Postgres blip must NOT
  trigger a pod restart — restarting would just re-add load to a DB that's
  already struggling. `tcpSocket` catches a gunicorn that has gone hard-down
  (no longer accepting connections) without punishing a healthy process that's
  momentarily waiting on the database. Liveness delay is longer (60s) to avoid
  restart loops during DB maintenance windows.

The worker and beat Deployments intentionally have **no HTTP probe**: Celery
workers have no HTTP health endpoint, and "waiting on the broker" is normal idle
behavior, not a failure. Health for background tasks is surfaced via Flower
(port 5555) or the Celery `inspect ping` healthcheck from
`docker-compose.yml`; add an HTTP probe here only if you expose a
worker-side endpoint.

## Migrations per boot / concurrency

`entrypoint.sh` runs `migrate --noinput` on every container start (web, worker,
beat). See [docs/migrations.md](migrations.md) for the full contract. The
transactional `django_migrations` table plus Postgres DDL locking bounds the
near-simultaneous boot race to a harmless "already applied" no-op; the
residual race is documented there as acceptable for this stack. If you need a
dedicated, human-gated migration step (least-privilege DB users, read-only
sidecars), gate the migrate call in `entrypoint.sh` behind a `MIGRATE_DISABLE`
env flag as described in `docs/migrations.md` ("When a
MIGRATE_DISABLE-style knob would be preferable").

## Validation

Two independent gates — neither requires a cluster:

1. **Standalone validator** (`scripts/validate_k8s_manifests.py`): Django-free,
   parses every YAML under `k8s/` with `yaml.safe_load` and asserts the
   structural invariants (kustomization refs exist + overlays point at `../base`,
   one Service per web Deployment with a matching selector/pods, both probes on
   port 8000, worker + worker + beat all `envFrom` both ConfigMap and Secret,
   beat/worker command overrides differ from web's gunicorn CMD, resource
   requests + limits on every container, `DJANGO_BEHIND_PROXY=1`). Run it
   directly or in a throwaway container:

   ```bash
   python3 scripts/validate_k8s_manifests.py
   # or, containerised (the web image has no k8s/):
   docker run --rm -v "$PWD":/mnt -w /mnt python:3.12-slim \
     sh -c 'python3 -m pip install --quiet pyyaml && python3 scripts/validate_k8s_manifests.py'
   ```

   CI runs this in the `docker` job as the "Validate Kubernetes manifests" step
   (next to the Grafana dashboard validator).

2. **Django test module** (`ecommerce/tests/test_k8s_manifests.py`): invokes the
   validator above through `make test`. It skips inside the app image (the
   `Dockerfile` only copies `be/ecommerce`, so `k8s/` + `scripts/` are absent —
   the skip message states this) and runs for real in CI's `test` job, where the
   full checkout is present.

### Manual pre-flight (kubectl required)

`kubectl` is **not** a CI dependency. The documented manual gate before applying
to a cluster is:

```bash
kubectl apply --dry-run=client -k k8s/overlays/prod
# or, field-by-field:
# kubectl apply --dry-run=client -f k8s/base/deployment-web.yaml ...
```

Both the validator and the dry-run gate must pass; they are complementary
(yaml structure vs. schema validity).

## Applying to a cluster

```bash
# dev
kubectl apply -k k8s/overlays/dev

# prod (override the tag to a real release sha, e.g. sha-99d53dc):
#   edit k8s/overlays/prod/kustomization.yaml -> images[].newTag, or pass:
kubectl kustomize build k8s/overlays/prod \
  --images ghcr.io/mjmichael73/django-ecommerce:sha-99d53dc \
  | kubectl apply -f -
```

## Blue-green roadmap

These manifests use single-colored selectors (`app: ecommerce-web`) on
intention. Task 9 will introduce a blue/green color-flip on top of these exact
labels without rewriting them — see the roadmap tick below.
