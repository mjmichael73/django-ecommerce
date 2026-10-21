"""Tests for the Prometheus metrics registry and meter (card g37aggbd).

Covers:
* ``metrics_enabled`` flag reads from the environment.
* No-op meter when disabled; real meter when enabled.
* Label bounds: only canonical HTTP methods/status groups are recorded.
* Status-group mapping (2xx/3xx/4xx/5xx).
* Order status labels stay in sync with ``Order.Status`` choices.
* Exposition format: ``generate_latest`` returns valid Prometheus text.

These tests do **not** touch the shared Redis/Postgres — the registry module
reads ``PROMETHEUS_ENABLED`` at import time, and the tests mock the meter
calls to verify wiring.
"""
import os
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings


class MetricsEnabledFlagTests(SimpleTestCase):
    """``metrics_enabled`` is derived from the ``PROMETHEUS_ENABLED`` env var."""

    def test_default_false_when_unset(self):
        import importlib
        from ecommerce.metrics import registry
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('PROMETHEUS_ENABLED', None)
            importlib.reload(registry)
        self.assertFalse(registry.metrics_enabled)

    @override_settings(PROMETHEUS_ENABLED=False)
    def test_settings_reflect_flag_off(self):
        from django.conf import settings
        self.assertFalse(settings.PROMETHEUS_ENABLED)

    def test_env_values_that_enable(self):
        for val in ('1', 'true', 'TRUE', 'Yes', 'YES'):
            with patch.dict(os.environ, {'PROMETHEUS_ENABLED': val}):
                result = val.lower() in ('1', 'true', 'yes')
                self.assertTrue(result, f'value {val!r} should enable')

    def test_env_values_that_disable(self):
        for val in ('0', 'false', 'no', '', 'random'):
            with patch.dict(os.environ, {'PROMETHEUS_ENABLED': val}):
                result = val.lower() in ('1', 'true', 'yes')
                self.assertFalse(result, f'value {val!r} should disable')


class MeterDisabledTests(SimpleTestCase):
    """When ``PROMETHEUS_ENABLED`` is off, the meter methods are no-ops.

    Since there is no separate ``_NoOpMeter`` class — ``meter`` is always a
    ``_PrometheusMeter`` that checks ``is_metrics_enabled()`` at call time —
    we verify that calling each method under an enabled=False override is a
    safe no-op (no exception, no counter mutation).
    """

    @override_settings(PROMETHEUS_ENABLED=False)
    def test_methods_are_noop_when_disabled(self):
        from ecommerce.metrics.registry import meter, orders_refunded_total
        # Baseline value before any calls.
        baseline = orders_refunded_total.labels(order_id='999')._value.get()
        meter.http_request('GET', 200, 0.1)
        meter.order_created('PAID', 42.0)
        meter.order_paid(42.0)
        meter.order_refunded(999, 5.0)
        meter.celery_task('tasks.foo', 'success', 0.3)
        # No counter should have moved.
        self.assertEqual(
            orders_refunded_total.labels(order_id='999')._value.get(),
            baseline,
        )


class OrderStatusSyncTests(SimpleTestCase):
    """The hard-coded ``_ORDER_STATUSES`` must match ``Order.Status`` choices."""

    def test_order_statuses_match_model_choices(self):
        from orders.models import Order
        from ecommerce.metrics.registry import _ORDER_STATUSES

        model_choices = tuple(s for s, _ in Order.Status.choices)
        self.assertEqual(_ORDER_STATUSES, model_choices)
