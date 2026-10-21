#!/usr/bin/env bash
#
# Blue-green deployment helper for the django-ecommerce web tier (card 828ff8ea).
#
# ONE color is live at all times: the `color` selector key on the ecommerce-web
# Service pins traffic to either `color: blue` (the base/default) or
# `color: green` (the green twin in k8s/blue-green/). This script performs the
# flip strictly AFTER the green Deployment has finished rolling out, and it
# NEVER patches anything but the Service selector.
#
# Ordering contract (DO NOT REORDER):
#   cutover:
#     1. kubectl apply -k k8s/overlays/<env>        # ensure blue world exists
#     2. kubectl apply -f k8s/blue-green/deployment-web-green.yaml
#     3. kubectl rollout status deploy/ecommerce-web-green  # green is READY
#     4. kubectl patch svc/ecommerce-web --type merge \
#            -p '{"spec":{"selector":{"color":"green"}}}'  # SINGLE flip
#   The readiness gate in step 3 is what enforces migration-aware ordering:
#   green pods boot, run `migrate --noinput` (entrypoint.sh), and pass their
#   `/health/` readiness probe BEFORE any traffic lands on them. See
#   docs/blue-green.md for the full gate ordering rationale.
#
# Safest usable flags: nounset + errtrace + a single pipefail-safe trap. We do
# NOT use `set -e` blindly (it makes backtick/command-substitution exits
# ambiguous); every command is checked explicitly with `die`.
set -u
set -o pipefail

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
usage() {
  cat <<'EOF'
usage: blue_green_deploy.sh <command> [env] [namespace]

commands:
  cutover   Apply the overlay + green twin, wait for green rollout, then flip
            the ecommerce-web Service selector to color: green.
  rollback  Flip the ecommerce-web Service selector back to color: blue
            (green pods are retained per the scaling policy in
            docs/blue-green.md).

args:
  env        dev | prod  (default: dev -> k8s/overlays/dev or prod)
  namespace  target namespace (default per-env below)
EOF
}

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Resolve the overlay path and a sane default namespace per environment.
overlay_for() {
  case "${1:-}" in
    dev)  echo "k8s/overlays/dev" ;;
    prod) echo "k8s/overlays/prod" ;;
    *)    echo "dev" ;;
  esac
}

namespace_for() {
  case "${1:-}" in
    dev)  echo "ecommerce-dev" ;;
    prod) echo "ecommerce-prod" ;;
    *)    echo "ecommerce" ;;
  esac
}

SERVICE_NAME="ecommerce-web"
GREEN_DEPLOYMENT="ecommerce-web-green"
ROLLOUT_TIMEOUT="${ROLLOUT_TIMEOUT:-300s}"   # overridable; docs cite 300s

# Only ever flip the Service selector's `color` to one of these two values.
VALID_COLORS="blue green"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
log()  { printf '[blue-green] %s\n' "$*"; }
err()  { printf '[blue-green][ERROR] %s\n' "$*" >&2; }

# die <message> <exit-code?>  — prints an error and exits non-zero.
die() {
  err "$1"
  exit "${2:-1}"
}

# have <command> — returns 0 if the command is on PATH.
have() { command -v "$1" >/dev/null 2>&1; }

require_kubectl() {
  have kubectl \
    || die "kubectl is required but not found on PATH" 127
}

# flip_selector <green|blue> — perform the single Service patch.
# Uses python3 to build a safe, exact JSON patch for ONLY spec.selector.color.
flip_selector() {
  local color="$1"
  local ok=0
  local c
  for c in $VALID_COLORS; do
    [ "$color" = "$c" ] && ok=1
  done
  [ "$ok" -eq 1 ] \
    || die "refusing to flip Service selector to unknown color '$color' (allowed: $VALID_COLORS)" 2

  # jq may not be present; python3 is universally available where kubectl runs.
  if have python3; then
    patch_json="$(python3 -c 'import json,sys; print(json.dumps({"spec":{"selector":{"color":sys.argv[1]}}}) )' "$color")"
  elif have jq; then
    patch_json="$(jq -nc --arg c "$color" '{spec:{selector:{color:$c}}}')"
  else
    die "need python3 or jq to build the JSON patch" 2
  fi

  log "patching Service/$SERVICE_NAME selector -> color: $color"
  kubectl patch "svc/$SERVICE_NAME" --type merge -p "$patch_json" \
    || die "failed to patch Service/$SERVICE_NAME selector to color: $color" 4
}

# ---------------------------------------------------------------------------
# cutover
# ---------------------------------------------------------------------------
cmd_cutover() {
  local env="${1:-dev}"
  local ns="${2:-$(namespace_for "$env")}"
  [ "$env" = "dev" ] || [ "$env" = "prod" ] \
    || die "unknown env '$env' (expected dev|prod)" 22

  require_kubectl
  local overlay; overlay="$(overlay_for "$env")"

  log "==> [1/4] apply $env overlay (blue world)"
  kubectl apply -k "$REPO_ROOT/$overlay" \
    || die "kubectl apply -k $overlay failed" 10

  log "==> [2/4] apply green twin"
  kubectl apply -f "$REPO_ROOT/k8s/blue-green/deployment-web-green.yaml" \
    || die "kubectl apply -f green twin failed" 11

  log "==> [3/4] wait for green Deployment rollout (migration-aware readiness gate)"
  # Green pods boot -> entrypoint.sh runs `migrate --noinput` -> readiness
  # httpGet /health/ (SELECT 1) passes. Only then does rollout status return.
  # This is the documented gate; see docs/blue-green.md.
  kubectl rollout status "deploy/$GREEN_DEPLOYMENT" --timeout="$ROLLOUT_TIMEOUT" -n "$ns" \
    || die "green Deployment rollout did not complete within $ROLLOUT_TIMEOUT (or failed)" 12

  log "==> [4/4] flip Service selector to color: green (traffic switch)"
  # The flip MUST happen only after step 3 completes. It touches only the
  # Service selector — no other resource is patched.
  flip_selector green

  log "cutover complete: traffic now served by color: green pods"
  log "verify: kubectl get pods,svc -n $ns --selector app=$SERVICE_NAME"
}

# ---------------------------------------------------------------------------
# rollback
# ---------------------------------------------------------------------------
cmd_rollback() {
  local env="${1:-dev}"
  local ns="${2:-$(namespace_for "$env")}"

  require_kubectl

  log "==> flip Service selector back to color: blue"
  # Blue pods are retained until the operator scales them down (see runbook
  # scaling policy); we only re-point traffic here. No green resource is deleted.
  flip_selector blue

  log "rollback complete: traffic now served by color: blue pods"
  log "green pods retained — scale down with:"
  log "  kubectl scale deploy/$GREEN_DEPLOYMENT --replicas=0 -n $ns"
}

# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------
main() {
  local command="${1:-}"
  shift || true
  case "$command" in
    cutover)   cmd_cutover "$@"   ;;
    rollback)  cmd_rollback "$@"  ;;
    -h|--help|help) usage         ;;
    "")        usage; die "no command given (cutover|rollback)" 2 ;;
    *)         usage; die "unknown command '$command' (cutover|rollback)" 2 ;;
  esac
}

main "$@"
