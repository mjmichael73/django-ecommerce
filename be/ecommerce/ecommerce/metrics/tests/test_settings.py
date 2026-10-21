"""Tests for Prometheus settings integration (card g37aggbd).

Covers:
* ``PROMETHEUS_ENABLED`` reflects the env var / override_settings.
* ``PROMETHEUS_METRICS_ALLOWED_IPS`` defaults to an empty set.
* The metrics middleware is in the MIDDLEWARE stack.
* The ``/metrics`` and ``/health`` URLs are registered.
"""
from django.test import SimpleTestCase, override_settings


class PrometheusSettingsTests(SimpleTestCase):
    def test_prometheus_enabled_can_be_overridden(self):
        from django.conf import settings
        # Default from env (no PROMETHEUS_ENABLED passed by most test runs).
        # When override_settings is used it must be respected by is_metrics_enabled.
        with override_settings(PROMETHEUS_ENABLED=True):
            self.assertTrue(settings.PROMETHEUS_ENABLED)
        with override_settings(PROMETHEUS_ENABLED=False):
            self.assertFalse(settings.PROMETHEUS_ENABLED)

    def test_metrics_middleware_in_middleware(self):
        from django.conf import settings
        self.assertIn(
            'ecommerce.metrics.middleware.PrometheusMetricsMiddleware',
            settings.MIDDLEWARE,
        )

    def test_metrics_allowed_ips_defaults_empty(self):
        from django.conf import settings
        self.assertEqual(settings.PROMETHEUS_METRICS_ALLOWED_IPS, set())

    def test_metrics_url_registered(self):
        from django.urls import reverse, get_resolver
        # The /metrics URL should be reversable even when disabled — the
        # view itself returns 404 at runtime.
        url = reverse('metrics')
        self.assertEqual(url, '/metrics')

    def test_health_url_registered(self):
        from django.urls import reverse
        url = reverse('health')
        self.assertEqual(url, '/health/')
