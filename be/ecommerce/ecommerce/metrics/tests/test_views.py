"""Tests for the Prometheus metrics and health endpoints (card g37aggbd).

Covers:
* ``/metrics`` returns 404 when ``PROMETHEUS_ENABLED`` is off.
* ``/metrics`` returns 200 + Prometheus exposition format when enabled.
* ``/metrics`` respects ``PROMETHEUS_METRICS_ALLOWED_IPS`` allowlist.
* ``/health`` returns 200 + JSON when the DB is reachable.
* ``/health`` returns 503 when the DB is down.
* Exposition content includes expected metric names.
"""
from unittest.mock import patch

from django.db import OperationalError
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from ecommerce.metrics.registry import meter  # noqa: F401 (ensure importable)


class HealthViewTests(TestCase):
    """``/health`` always available — liveness/readiness probe."""

    def setUp(self):
        self.client = Client()

    def test_200_when_db_ok(self):
        url = reverse('health')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'ok')
        self.assertEqual(data['database'], 'ok')
        self.assertIn('timestamp', data)

    @patch('django.db.backends.base.base.BaseDatabaseWrapper.cursor')
    def test_503_when_db_down(self, mock_cursor):
        mock_cursor.side_effect = OperationalError('connection refused')
        url = reverse('health')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 503)
        data = response.json()
        self.assertEqual(data['status'], 'unavailable')
        self.assertEqual(data['database'], 'unavailable')


class MetricsEndpointDisabledTests(TestCase):
    """When metrics are disabled, /metrics 404s."""

    def setUp(self):
        self.client = Client()

    def test_metrics_404_when_disabled(self):
        url = reverse('metrics')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)


@override_settings(PROMETHEUS_ENABLED=True)
class MetricsEndpointEnabledTests(TestCase):
    """When ``PROMETHEUS_ENABLED=1`` the /metrics endpoint returns exposition."""

    def setUp(self):
        self.client = Client()

    def test_metrics_returns_exposition(self):
        response = self.client.get('/metrics')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response['Content-Type'],
            'text/plain; version=0.0.4; charset=utf-8',
        )
        body = response.content.decode()
        # Check for known metric names.
        self.assertIn('django_http_requests_total', body)
        self.assertIn('# HELP', body)
        self.assertIn('# TYPE', body)

    def test_metrics_includes_order_and_celery_metrics(self):
        response = self.client.get('/metrics')
        body = response.content.decode()
        self.assertIn('django_orders_total', body)
        self.assertIn('django_orders_revenue_total', body)
        self.assertIn('django_celery_tasks_total', body)
        self.assertIn('django_celery_task_duration_seconds', body)


@override_settings(PROMETHEUS_ENABLED=True)
class MetricsEndpointAllowedIpsTests(TestCase):
    """``PROMETHEUS_METRICS_ALLOWED_IPS`` restricts scrape access."""

    def setUp(self):
        self.client = Client()

    def test_403_when_ip_not_allowed(self):
        with override_settings(
            PROMETHEUS_METRICS_ALLOWED_IPS=frozenset({'10.0.0.1'}),
        ):
            response = self.client.get('/metrics')
            self.assertEqual(response.status_code, 403)

    def test_200_when_ip_allowed(self):
        with override_settings(
            PROMETHEUS_METRICS_ALLOWED_IPS=frozenset({'127.0.0.1'}),
        ):
            response = self.client.get('/metrics')
            self.assertEqual(response.status_code, 200)
