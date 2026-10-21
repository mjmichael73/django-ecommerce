#!/usr/bin/env python3
"""Standalone validator for Grafana dashboards and Prometheus provisioning.

Card 3c3b2e3a — Phase 4.1 monitoring: Grafana dashboards.

Run this script to validate the monitoring artefacts without starting Django:

    python3 docker/grafana/scripts/validate_dashboards.py

Exits 0 if all checks pass, 1 otherwise. Validates:

1. Every JSON file in docker/grafana/dashboards/ is valid JSON.
2. Each dashboard has required top-level keys (annotations, panels, etc.).
3. Each dashboard has a unique ``uid``.
4. Every panel target has a non-empty PromQL ``expr``.
5. Every panel references at least one app-defined metric.
6. Every panel uses the Prometheus datasource.
7. docker/prometheus/prometheus.yml references ``web:8000`` and ``/metrics``.
8. The Grafana datasource and dashboard provisioning YAML files have the
   expected structural elements.

This mirrors the Django test suite in
``ecommerce/metrics/tests/test_grafana_provisioning.py`` so both paths
(validate locally / validate via ``make test``) stay in sync.
"""
import json
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# This file lives at: docker/grafana/scripts/validate_dashboards.py
# So repo root is three levels up.
REPO_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", ".."))
DOCKER_DIR = os.path.join(REPO_ROOT, "docker")

DASHBOARD_DIR = os.path.join(DOCKER_DIR, "grafana", "dashboards")
PROMETHEUS_FILE = os.path.join(DOCKER_DIR, "prometheus", "prometheus.yml")
DATASOURCE_FILE = os.path.join(
    DOCKER_DIR, "grafana", "provisioning", "datasources", "datasource.yml"
)
DASHBOARD_PROV_FILE = os.path.join(
    DOCKER_DIR, "grafana", "provisioning", "dashboards", "dashboard-provider.yml"
)

REQUIRED_DASH_KEYS = {"annotations", "panels", "schemaVersion", "title", "uid", "version"}

# The canonical metric names are defined in ecommerce/metrics/registry.py's
# __all__ list. When the validator can import the registry (i.e. it runs inside
# a Django environment or the module is on PYTHONPATH), we cross-check this
# static list against the registry so stale metric names are caught. When the
# import fails (standalone usage without Django), the static list still
# provides a useful baseline.
STATIC_APP_METRICS = {
    "django_http_requests_total",
    "django_http_request_duration_seconds",
    "django_orders_total",
    "django_orders_revenue_total",
    "django_orders_refunded_total",
    "django_celery_tasks_total",
    "django_celery_task_duration_seconds",
}


def _load_known_metrics():
    """Return the set of app-defined metric names.

    Tries to import the real registry (``ecommerce.metrics.registry``) so the
    known set is always derived from the source of truth. Falls back to the
    static list when Django is not importable (standalone CLI usage).
    """
    try:
        import importlib

        # Allow importing from the bind-mounted ecommerce package without a
        # full Django settings module — we only need the module-level names.
        # The validator is called from Django tests (where the path is on
        # sys.path) and from the standalone CLI (where it is not importable).
        mod = importlib.import_module("ecommerce.metrics.registry")
        metric_names = set()
        for attr_name in getattr(mod, "__all__", []):
            obj = getattr(mod, attr_name, None)
            if obj is None:
                continue
            # prometheus_client Counter/Histogram objects store the base name
            # in ``_name``. Counters have ``_total`` appended in the exposition
            # format (the suffix is stripped from ``_name``), so we add it back
            # for counters. Histograms are exposed with their ``_name`` plus
            # ``_bucket``/``_sum``/``_count`` suffixes.
            if hasattr(obj, "_name") and hasattr(obj, "_type"):
                base = obj._name
                if obj._type == "counter" and not base.endswith("_total"):
                    base = base + "_total"
                metric_names.add(base)
        if metric_names:
            return metric_names
    except Exception:
        pass
    return STATIC_APP_METRICS


# Histogram metrics expose `_bucket` suffixed series; these are derived
# metrics and should pass validation alongside their base names.
_DERIVED_SUFFIXES = ("_bucket", "_sum", "_count")


def fail(msg):
    print(f"  FAIL: {msg}", file=sys.stderr)
    return False


def validate_dashboards():
    ok = True
    if not os.path.isdir(DASHBOARD_DIR):
        ok = fail(f"Dashboard directory not found: {DASHBOARD_DIR}")
        return ok

    files = sorted(
        f for f in os.listdir(DASHBOARD_DIR) if f.endswith(".json")
    )
    if not files:
        ok = fail(f"No dashboard JSON files in {DASHBOARD_DIR}")
        return ok

    print(f"Validating {len(files)} dashboard(s)...")

    # Resolve the canonical metric set from the registry if importable;
    # otherwise fall back to the static list.
    known_metrics = _load_known_metrics()
    if known_metrics != STATIC_APP_METRICS:
        print(f"  (Loaded {len(known_metrics)} metrics from registry source of truth)")
    else:
        print(f"  (Using static metric list — registry not importable)")

    uids = []
    for fname in files:
        path = os.path.join(DASHBOARD_DIR, fname)
        try:
            with open(path) as f:
                dash = json.load(f)
        except json.JSONDecodeError as e:
            ok = fail(f"{fname} is not valid JSON: {e}")
            continue

        # Required keys
        missing = REQUIRED_DASH_KEYS - set(dash.keys())
        if missing:
            ok = fail(f"{fname} missing keys: {missing}")

        # Unique UID
        uid = dash.get("uid")
        if not uid:
            ok = fail(f"{fname} has no uid")
        elif uid in uids:
            ok = fail(f"{fname} has duplicate uid: {uid}")
        else:
            uids.append(uid)

        # Panels
        panels = dash.get("panels", [])
        if not panels:
            ok = fail(f"{fname} has no panels")

        for panel in panels:
            title = panel.get("title", "<unnamed>")
            targets = panel.get("targets", [])
            if not targets:
                ok = fail(f"{fname}: panel '{title}' has no targets")
                continue

            for target in targets:
                expr = target.get("expr", "")
                if not expr.strip():
                    ok = fail(f"{fname}: panel '{title}' has empty expr")
                tokens = set(re.findall(r"[a-z_][a-z0-9_]+", expr))
                # Also match derived metric names (e.g. _bucket, _sum, _count).
                matched = set()
                for token in tokens:
                    for base in known_metrics:
                        if token == base or token.startswith(base + "_"):
                            matched.add(base)
                if not matched:
                    ok = fail(
                        f"{fname}: panel '{title}' does not reference a known "
                        f"app metric. Expr: {expr}"
                    )

            ds = panel.get("datasource", {})
            if ds.get("type") != "prometheus":
                ok = fail(
                    f"{fname}: panel '{title}' uses non-prometheus datasource: {ds}"
                )

        print(f"  OK: {fname} ({len(panels)} panels, uid={uid})")

    return ok


def validate_prometheus():
    print("Validating Prometheus config...")
    if not os.path.isfile(PROMETHEUS_FILE):
        return fail(f"Prometheus config not found: {PROMETHEUS_FILE}")

    with open(PROMETHEUS_FILE) as f:
        content = f.read()

    checks = {
        "scraper targets web:8000": "web:8000" in content,
        "defines scrape_configs": "scrape_configs" in content,
        "references /metrics path": "/metrics" in content,
    }
    ok = True
    for desc, passed in checks.items():
        if not passed:
            ok = fail(f"prometheus.yml: {desc}")
    if ok:
        print(f"  OK: {PROMETHEUS_FILE}")
    return ok


def validate_provisioning_yaml():
    print("Validating Grafana provisioning files...")
    ok = True

    if not os.path.isfile(DATASOURCE_FILE):
        ok = fail(f"Datasource provisioning not found: {DATASOURCE_FILE}")
    else:
        with open(DATASOURCE_FILE) as f:
            content = f.read()
        checks = {
            "Prometheus datasource name": "name: Prometheus" in content,
            "Prometheus type": "type: prometheus" in content,
            "isDefault: true": "isDefault: true" in content,
        }
        for desc, passed in checks.items():
            if not passed:
                ok = fail(f"datasource.yml: {desc}")
        if ok:
            print(f"  OK: {DATASOURCE_FILE}")

    if not os.path.isfile(DASHBOARD_PROV_FILE):
        ok = fail(f"Dashboard provisioning not found: {DASHBOARD_PROV_FILE}")
    else:
        with open(DASHBOARD_PROV_FILE) as f:
            content = f.read()
        checks = {
            "apiVersion: 1": "apiVersion: 1" in content,
            "providers section": "providers:" in content,
            "provider name 'default'": "name: 'default'" in content or "name: default" in content,
            "type: file": "type: file" in content,
            "dashboards path": "/etc/grafana/provisioning/dashboards" in content,
        }
        for desc, passed in checks.items():
            if not passed:
                ok = fail(f"dashboard-provider.yml: {desc}")
        if ok:
            print(f"  OK: {DASHBOARD_PROV_FILE}")

    return ok


def main():
    print(f"Repo root: {REPO_ROOT}")
    print(f"Dashboard dir: {DASHBOARD_DIR}")
    print()

    results = [
        validate_dashboards(),
        validate_prometheus(),
        validate_provisioning_yaml(),
    ]

    print()
    if all(results):
        print("✅ All monitoring artefacts validated successfully.")
        return 0
    else:
        print("❌ Some monitoring artefacts failed validation.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
