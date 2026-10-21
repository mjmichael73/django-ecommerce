"""Validation tests for Grafana dashboard and Prometheus provisioning files.

Card 3c3b2e3a — Phase 4.1 monitoring: Grafana dashboards.

These tests run as part of the Django test suite (via ``make test`` / the CI
workflow) and validate the structural integrity of the monitoring artefacts
shipped under ``docker/``:

* Every dashboard JSON file is valid JSON and conforms to the Grafana
  provisioning model (required top-level keys, each panel has a valid PromQL
  expression, datasource references match the provisioned datasource).
* The Prometheus ``prometheus.yml`` contains a scrape job targeting the Django
  ``web`` service's ``/metrics`` path.
* The Grafana datasource and dashboard provisioning files contain the expected
  structural elements.

These tests do **not** start Docker; they operate purely on the files on disk
so they run in any environment (CI, local venv, or inside the web container).
"""
import json
import os
import re

from django.test import SimpleTestCase

# Resolve the docker/ directory from the repo root.
#
# The monitoring artefacts live at <repo-root>/docker/. During tests the repo
# root may not be discoverable from __file__ (e.g. when running inside a
# Docker bind-mount where be/ecommerce is mounted at /app and the docker/
# directory is not visible inside the container).
#
# Resolution order:
# 1. ECOMMERCE_DOCKER_DIR env var — set this if the test harness cannot
#    auto-discover the directory (e.g. running inside the web container with
#    a bind-mount).
# 2. Default: navigate up from this file's location to the repo root.
#    be/ecommerce/ecommerce/metrics/tests/test_grafana_provisioning.py
#    -> 5 levels up = repo root
_ECOMMERCE_DOCKER_DIR = os.environ.get("ECOMMERCE_DOCKER_DIR")

if _ECOMMERCE_DOCKER_DIR:
    DOCKER_DIR = _ECOMMERCE_DOCKER_DIR
else:
    _THIS_DIR = os.path.dirname(os.path.abspath(__file__))
    # tests -> metrics -> ecommerce -> be(ecommerce) -> repo_root
    _REPO_ROOT = os.path.normpath(os.path.join(_THIS_DIR, "..", "..", "..", "..", ".."))
    DOCKER_DIR = os.path.join(_REPO_ROOT, "docker")

DASHBOARD_DIR = os.path.join(DOCKER_DIR, "grafana", "dashboards")
PROMETHEUS_FILE = os.path.join(DOCKER_DIR, "prometheus", "prometheus.yml")
DATASOURCE_FILE = os.path.join(
    DOCKER_DIR, "grafana", "provisioning", "datasources", "datasource.yml"
)
DASHBOARD_PROV_FILE = os.path.join(
    DOCKER_DIR, "grafana", "provisioning", "dashboards", "dashboard-provider.yml"
)

# Required top-level keys for a Grafana dashboard JSON model.
REQUIRED_DASH_KEYS = {"annotations", "panels", "schemaVersion", "title", "uid", "version"}

# Metrics emitted by the Django app (see ecommerce/metrics/registry.py).
# This must stay in sync with the Counter/Histogram objects defined in
# registry.py. The RegistryMetricSourceTests class below enforces this at
# test time by reflecting on the registry module itself.
KNOWN_APP_METRICS = {
    "django_http_requests_total",
    "django_http_request_duration_seconds",
    "django_orders_total",
    "django_orders_revenue_total",
    "django_orders_refunded_total",
    "django_celery_tasks_total",
    "django_celery_task_duration_seconds",
}

def _list_dashboard_files():
    """Return all dashboard JSON files, or raise AssertionError."""
    if not os.path.isdir(DASHBOARD_DIR):
        raise AssertionError(f"Dashboard directory not found: {DASHBOARD_DIR}")
    files = [
        os.path.join(DASHBOARD_DIR, f)
        for f in sorted(os.listdir(DASHBOARD_DIR))
        if f.endswith(".json")
    ]
    if not files:
        raise AssertionError(f"No dashboard JSON files found in {DASHBOARD_DIR}")
    return files


def _load_dashboards():
    """Return a list of (filename, dict) for every dashboard."""
    result = []
    for path in _list_dashboard_files():
        with open(path) as f:
            result.append((os.path.basename(path), json.load(f)))
    return result


class GrafanaDashboardValidationTests(SimpleTestCase):
    """Validate every dashboard JSON file under docker/grafana/dashboards/."""

    def test_all_dashboard_files_are_valid_json(self):
        """Every dashboard file parses as valid JSON."""
        for name, dash in _load_dashboards():
            self.assertIsInstance(dash, dict, f"{name} is not a JSON object")

    def test_dashboards_have_required_keys(self):
        """Each dashboard has the required Grafana schema keys."""
        for name, dash in _load_dashboards():
            missing = REQUIRED_DASH_KEYS - set(dash.keys())
            self.assertFalse(
                missing,
                f"{name} missing required keys: {missing}",
            )

    def test_dashboards_have_unique_uids(self):
        """No two dashboards share the same ``uid``."""
        uids = [dash.get("uid") for _, dash in _load_dashboards()]
        self.assertEqual(
            len(uids), len(set(uids)),
            f"Duplicate dashboard UIDs detected: {uids}",
        )

    def test_dashboards_have_at_least_one_panel(self):
        """Each dashboard has at least one panel."""
        for name, dash in _load_dashboards():
            self.assertGreater(
                len(dash.get("panels", [])), 0,
                f"{name} has no panels",
            )

    def test_panel_targets_have_promql_expressions(self):
        """Every panel's query target contains a non-empty PromQL ``expr``."""
        for name, dash in _load_dashboards():
            for panel in dash.get("panels", []):
                targets = panel.get("targets", [])
                self.assertTrue(
                    targets,
                    f"Panel '{panel.get('title')}' in {name} has no targets",
                )
                for target in targets:
                    expr = target.get("expr", "")
                    self.assertTrue(
                        expr.strip(),
                        f"Panel '{panel.get('title')}' in {name} has empty PromQL expr",
                    )

    def test_panel_targets_reference_known_metrics(self):
        """PromQL expressions reference at least one metric from the app."""
        for name, dash in _load_dashboards():
            for panel in dash.get("panels", []):
                for target in panel.get("targets", []):
                    expr = target.get("expr", "")
                    tokens = set(re.findall(r"[a-z_][a-z0-9_]+", expr))
                    # Match base metric names and derived suffixes (_bucket, _sum, _count).
                    matched = set()
                    for token in tokens:
                        for base in KNOWN_APP_METRICS:
                            if token == base or token.startswith(base + "_"):
                                matched.add(base)
                                break
                    self.assertTrue(
                        matched,
                        f"Panel '{panel.get('title')}' in {name} does not reference "
                        f"any known app metric. Expr: {expr}",
                    )

    def test_panel_datasource_is_prometheus(self):
        """Each panel's datasource references the Prometheus type."""
        for name, dash in _load_dashboards():
            for panel in dash.get("panels", []):
                ds = panel.get("datasource", {})
                self.assertEqual(
                    ds.get("type"), "prometheus",
                    f"Panel '{panel.get('title')}' in {name} uses non-prometheus "
                    f"datasource: {ds}",
                )


class PrometheusProvisioningTests(SimpleTestCase):
    """Validate the Prometheus configuration file."""

    def test_prometheus_file_exists(self):
        self.assertTrue(
            os.path.isfile(PROMETHEUS_FILE),
            f"Prometheus config not found: {PROMETHEUS_FILE}",
        )

    def test_prometheus_has_scrape_target(self):
        """The config file references the web service and /metrics path."""
        with open(PROMETHEUS_FILE) as f:
            content = f.read()
        self.assertIn("web:8000", content, "prometheus.yml must scrape web:8000")
        self.assertIn("scrape_configs", content, "prometheus.yml must define scrape_configs")
        self.assertIn("/metrics", content, "prometheus.yml must reference /metrics path")


class GrafanaProvisioningTests(SimpleTestCase):
    """Validate the Grafana provisioning YAML files (structural checks)."""

    def test_datasource_file_contains_prometheus(self):
        """datasource.yml contains a Prometheus datasource entry."""
        self.assertTrue(
            os.path.isfile(DATASOURCE_FILE),
            f"Datasource provisioning not found: {DATASOURCE_FILE}",
        )
        with open(DATASOURCE_FILE) as f:
            content = f.read()
        self.assertIn("- name: Prometheus", content)
        self.assertIn("type: prometheus", content)
        self.assertIn("isDefault: true", content)

    def test_dashboard_provider_file_has_expected_structure(self):
        """dashboard-provider.yml has the expected provider structure."""
        self.assertTrue(
            os.path.isfile(DASHBOARD_PROV_FILE),
            f"Dashboard provisioning not found: {DASHBOARD_PROV_FILE}",
        )
        with open(DASHBOARD_PROV_FILE) as f:
            content = f.read()
        self.assertIn("apiVersion: 1", content)
        self.assertIn("providers:", content)
        self.assertIn("- name: 'default'", content)
        self.assertIn("type: file", content)
        self.assertIn("path: /etc/grafana/provisioning/dashboards", content)


class DashboardCoverageTests(SimpleTestCase):
    """Verify the three required dashboard categories are covered."""

    def _dashboard_titles(self):
        return [dash.get("title", "") for _, dash in _load_dashboards()]

    def test_http_dashboard_exists(self):
        """There is a dashboard covering HTTP health/latency/errors."""
        titles = self._dashboard_titles()
        http_dashes = [t for t in titles if any(
            kw in t.lower() for kw in ["http", "latency", "health", "error"]
        )]
        self.assertTrue(http_dashes, f"No HTTP dashboard found. Titles: {titles}")

    def test_business_dashboard_exists(self):
        """There is a dashboard covering orders/payments/refunds."""
        titles = self._dashboard_titles()
        biz_dashes = [t for t in titles if any(
            kw in t.lower() for kw in ["order", "payment", "refund", "business", "revenue"]
        )]
        self.assertTrue(biz_dashes, f"No business dashboard found. Titles: {titles}")

    def test_celery_dashboard_exists(self):
        """There is a dashboard covering Celery task health/latency."""
        titles = self._dashboard_titles()
        celery_dashes = [t for t in titles if "celery" in t.lower()]
        self.assertTrue(celery_dashes, f"No Celery dashboard found. Titles: {titles}")


class RegistryMetricSourceTests(SimpleTestCase):
    """Verify KNOWN_APP_METRICS matches the actual metrics registry.

    The static ``KNOWN_APP_METRICS`` set in this test module (and in the
    standalone ``validate_dashboards.py`` script) must always match the real
    metric names defined in ``ecommerce.metrics.registry``. This test reflects
    on the registry module to extract the Counter/Histogram object names and
    cross-checks them, catching drift where a dashboard references a metric
    name that no longer exists (or vice-versa).
    """

    def test_static_metric_names_match_registry(self):
        """KNOWN_APP_METRICS reflects the real registry's exported metrics."""
        from ecommerce.metrics import registry as metrics_registry

        # Extract metric names from Counter/Histogram objects defined in the
        # registry module. prometheus_client stores the base name in ``_name``;
        # for Counter objects the ``_total`` suffix is appended at exposition
        # time (stripped from ``_name``), so we add it back here.
        actual = set()
        for attr_name in metrics_registry.__all__:
            obj = getattr(metrics_registry, attr_name, None)
            if obj is None:
                continue
            if hasattr(obj, "_name") and hasattr(obj, "_type"):
                base = obj._name
                if obj._type == "counter" and not base.endswith("_total"):
                    base = base + "_total"
                actual.add(base)

        self.assertEqual(
            KNOWN_APP_METRICS,
            actual,
            "KNOWN_APP_METRICS is out of sync with ecommerce.metrics.registry. "
            f"Static set has: {KNOWN_APP_METRICS - actual} extra, "
            f"Registry has: {actual - KNOWN_APP_METRICS} missing.",
        )
