"""Tests for Celery task metrics instrumentation (card g37aggbd).

Covers:
* The ``_tracing_call`` wrapper in ``ecommerce/celery.py`` records task
  duration and success/failure status via the meter.
* Success path: record_celery_task called with ``'success'``.
* Failure path: record_celery_task called with ``'failure'``, exception
  re-raised.
* No crash when prometheus_client / metrics are disabled.
"""
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings


class CeleryTracingMetricTests(SimpleTestCase):
    """Verify the ``_tracing_call`` wrapper records metrics."""

    def test_records_success(self):
        from ecommerce.celery import _tracing_call
        from ecommerce.metrics.registry import meter

        class FakeRequest:
            headers = {}

        class FakeTask:
            name = 'orders.order_created'
            request = FakeRequest()

        fake = FakeTask()
        with patch('ecommerce.celery._task_call', return_value='ok') as mock_call, \
             patch.object(meter, 'celery_task') as mock_record:
            result = _tracing_call(fake, (), {})
        self.assertEqual(result, 'ok')
        mock_record.assert_called_once()
        args = mock_record.call_args
        self.assertEqual(args.args[0], 'orders.order_created')
        self.assertEqual(args.args[1], 'success')
        self.assertIsInstance(args.args[2], float)

    def test_records_failure_and_reraises(self):
        from ecommerce.celery import _tracing_call
        from ecommerce.metrics.registry import meter

        class FakeRequest:
            headers = {}

        class FakeTask:
            name = 'orders.order_created'
            request = FakeRequest()

        def boom(*a, **kw):
            raise ValueError('task crashed')

        fake = FakeTask()
        with patch('ecommerce.celery._task_call', side_effect=boom) as mock_call, \
             patch.object(meter, 'celery_task') as mock_record:
            with self.assertRaises(ValueError):
                _tracing_call(fake, (), {})
        mock_record.assert_called_once()
        args = mock_record.call_args
        self.assertEqual(args.args[0], 'orders.order_created')
        self.assertEqual(args.args[1], 'failure')


@override_settings(PROMETHEUS_ENABLED=False)
class CeleryTracingDisabledTests(SimpleTestCase):
    """When metrics are disabled, the tracing wrapper still works."""

    def test_tracing_still_functions_when_disabled(self):
        from ecommerce.celery import _tracing_call

        class FakeRequest:
            headers = {}

        class FakeTask:
            name = 'orders.test_task'
            request = FakeRequest()

        fake = FakeTask()
        with patch('ecommerce.celery._task_call', return_value='ok'):
            result = _tracing_call(fake, (), {})
        self.assertEqual(result, 'ok')
        # The metric call should have been made but absorbed by the meter's
        # internal is_metrics_enabled check (no error).
