# Blue-Green Deployment — Django E-commerce

Card 828ff8ea — Phase 5 deployment hardening. This runbook sits on top of the
single-colored Kustomize manifests shipped by Task 8 (see
[docs/kubernetes.md](kubernetes.md) for the base layout). **One color is live
at all times:** a single `color` selector key on the `ecommerce-web` Service
points ClusterIP traffic to either the **blue** world (the base/default) or the
**green** world (the green twin in `k8s/blue-green/`).

## What "blue-green" costs here

This is the **minimal** blue-green: a color-flip on the web Service only.

- Worker / beat Deployments stay colorless (no green twin).
- No Ingress changes, no Service Mesh, no second load balancer, no DNS swap.
- The flip touches **only** `service-web`'s `selector.color`. Nothing else.

## Color mechanism

| Resource | Role |
|----------|------|
| `k8s/base/deployment-web.yaml` | BLUE web Deployment; pod template + matchLabels carry `color: blue`. |
| `k8s/base/service-web.yaml` | ClusterIP 80 → 8000; selector `{app: ecommerce-web, color: blue}` (shipped default). |
| `k8s/blue-green/deployment-web-green.yaml` | GREEN twin — `name: ecommerce-web-green`, `color: green` labels, otherwise identical (probes, envFrom config+secret, port 8000, resources, entrypoint/migrate contract). |

**Base stays single-colored:** the green twin is NOT listed in
`k8s/base/kustomization.yaml`. A plain `kubectl apply -k k8s/overlays/<env>`
only ever brings up the blue world. The green twin is applied by path:

```bash
kubectl apply -f k8s/blue-green/deployment-web-green.yaml
```

## Cutover (deploy new image without downtime)

1. `kubectl apply -k k8s/overlays/<env>`  — ensure the blue world is present.
2. `kubectl apply -f k8s/blue-green/deployment-web-green.yaml`  — stand up green.
3. `kubectl rollout status deploy/ecommerce-web-green --timeout=300s`  — **migration-aware readiness gate**.
4. `kubectl patch svc/ecommerce-web --type merge -p '{"spec":{"selector":{"color":"green"}}}'`  — single flip.

> `scripts/blue_green_deploy.sh cutover <dev|prod>` runs steps 1–4 in order and
> refuses to proceed past step 3 until the green rollout is ready.

### Why green boots and migrates BEFORE the switch (the gate ordering)

The readiness probe on the green Deployment is `httpGet /health/` on port 8000,
which runs `SELECT 1` against Postgres. That probe only succeeds once:

1. The green pod has started the container, AND
2. `entrypoint.sh` has run `migrate --noinput` (boot migrate), AND
3. gunicorn is serving `/health/` and the DB check passes.

`kubectl rollout status` (step 3) returns only when the readiness probe passes for
enough replicas. Therefore the Service selector flip in step 4 can never send
traffic to a green pod that has not already completed its boot-migrate. This is the
documented ordering; **do not** reorder the steps — the script enforces it and so
must any hand-rolled manual cutover.

### Schema-compatibility requirement (expand-first)

Because green pods boot-migrate on start, a green pod may run a newer migration
than the still-live blue pods. That is safe **only** because every schema change
in this project follows the expand-first contract in
[docs/migrations.md](migrations.md) (Phase A — Expand: backward-compatible,
additive changes; the old image tolerates the widened schema). If a migration
is NOT expand-safe, do it as a separate, human-gated deploy step (see
"Migrations strategy" in [docs/production.md](production.md)) BEFORE the green
cutover, and verify the blue world is still serving against the widened schema
first.

## Rollback (flip traffic back to blue)

1. `kubectl patch svc/ecommerce-web --type merge -p '{"spec":{"selector":{"color":"blue"}}}'`

> `scripts/blue_green_deploy.sh rollback <dev|prod>` does exactly this one flip.

Blue pods were **never removed** during cutover, so they are still running and
immediately resume serving traffic. Green pods are **retained** (not deleted) by
the script — this is deliberate:

### Scaling policy for the idle color

| After flip to… | Idle color retained | Operator action |
|----------------|---------------------|-----------------|
| green | blue | keep blue for N minutes (default ~5–10), then scale down |
| blue (rollback) | green | keep green for the validation window, then scale down |

Scale the idle color down explicitly once you are confident:

```bash
kubectl scale deploy/ecommerce-web-green --replicas=0   # after flip to green
kubectl scale deploy/ecommerce-web --replicas=0         # after rollback to blue
```

`delete` on the idle Deployment is safe once scaled to 0 (it only removes the
replica set / pods; the retained color was always a full Deployment spec).

## kubectl cheat-sheet

```bash
# Apply the blue world for an environment
kubectl apply -k k8s/overlays/dev      # or k8s/overlays/prod

# Stand up green
kubectl apply -f k8s/blue-green/deployment-web-green.yaml

# Watch green roll out
kubectl rollout status deploy/ecommerce-web-green --timeout=300s
kubectl get pods -n <ns> -l app=ecommerce-web,color=green -w

# Flip traffic (the ONLY resource the flip touches)
kubectl patch svc/ecommerce-web --type merge \
  -p '{"spec":{"selector":{"color":"green"}}}'

# Flip back to blue (rollback)
kubectl patch svc/ecommerce-web --type merge \
  -p '{"spec":{"selector":{"color":"blue"}}}'

# Done — scale the idle color down
kubectl scale deploy/ecommerce-web-green --replicas=0
# or, after rollback:
kubectl scale deploy/ecommerce-web --replicas=0
```

## Environments & overlays

| Env | Overlay path | Default namespace | Notes |
|-----|--------------|-------------------|-------|
| dev | `k8s/overlays/dev` | `ecommerce-dev` | DEBUG=1, 1 web replica (blue), placeholder secrets. |
| prod | `k8s/overlays/prod` | `ecommerce-prod` | DEBUG=0, 3 web replicas (blue), tightened limits. Override the GHCR tag to a release sha (see [docs/kubernetes.md](kubernetes.md)). |

The helper defaults `env` to `dev` and `namespace` to the per-env default above;
both are overridable as positional args:

```bash
scripts/blue_green_deploy.sh cutover prod ecommerce-prod
scripts/blue_green_deploy.sh rollback dev ecommerce
```

## Validation & dry-run

- **Static validator** (no cluster): `scripts/validate_k8s_manifests.py`
  (run containerised; see [docs/kubernetes.md](kubernetes.md) → Validation). It
  asserts the service selector carries `color`, base web labels `color: blue`,
  the green twin's name/color/probes/envFrom/port, the ordered gate in the
  helper script, and that the flip only flips between blue/green.
- **Manual pre-flight** (kubectl required, human gate):
  ```bash
  kubectl apply --dry-run=client -k k8s/overlays/prod
  kubectl apply --dry-run=client -f k8s/blue-green/deployment-web-green.yaml
  ```

`kubectl` is **not** a CI dependency — validation stays static; the dry-run is a
documented manual gate only.
