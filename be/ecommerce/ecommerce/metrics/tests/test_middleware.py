"""Tests for the Prometheus metrics middleware (card g37aggbd).

Covers:
* HTTP request recording: method + status-group labels are passed to the meter.
* ``/metrics`` path is excluded (no self-referential request count).
* Non-standard HTTP methods are ignored (label cardinality bound).
* Exception in the view still records a 500 request.
* Pass-through when metrics are disabled (no meter calls).
"""
from unittest.mock import patch

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings


def _dummy_view(status=200):
    def view(request):
        return HttpResponse('ok', status=status)
    return view


class PrometheusMiddlewareTests(SimpleTestCase):
    """Unit tests for ``PrometheusMetricsMiddleware`` via RequestFactory."""

    def setUp(self):
        self.factory = RequestFactory()

    @patch('ecommerce.metrics.middleware.is_metrics_enabled', return_value=True)
    @patch('ecommerce.metrics.middleware.meter')
    def test_records_get_2xx(self, mock_meter, _enabled_mock):
        from ecommerce.metrics.middleware import PrometheusMetricsMiddleware
        request = self.factory.get('/')
        mw = PrometheusMetricsMiddleware(_dummy_view(200))
        mw(request)
        mock_meter.http_request.assert_called_once()
        args = mock_meter.http_request.call_args
        self.assertEqual(args.args[0], 'GET')
        self.assertEqual(args.args[1], 200)
        self.assertIsInstance(args.args[2], float)
        self.assertGreaterEqual(args.args[2], 0.0)

    @patch('ecommerce.metrics.middleware.is_metrics_enabled', return_value=True)
    @patch('ecommerce.metrics.middleware.meter')
    def test_records_post_4xx(self, mock_meter, _enabled_mock):
        from ecommerce.metrics.middleware import PrometheusMetricsMiddleware
        request = self.factory.post('/')
        mw = PrometheusMetricsMiddleware(_dummy_view(404))
        mw(request)
        args = mock_meter.http_request.call_args
        self.assertEqual(args.args[0], 'POST')
        self.assertEqual(args.args[1], 404)

    @patch('ecommerce.metrics.middleware.is_metrics_enabled', return_value=True)
    @patch('ecommerce.metrics.middleware.meter')
    def test_skips_metrics_path(self, mock_meter, _enabled_mock):
        from ecommerce.metrics.middleware import PrometheusMetricsMiddleware
        request = self.factory.get('/metrics')
        mw = PrometheusMetricsMiddleware(_dummy_view(200))
        mw(request)
        mock_meter.http_request.assert_not_called()

    @patch('ecommerce.metrics.middleware.is_metrics_enabled', return_value=True)
    @patch('ecommerce.metrics.middleware.meter')
    def test_ignores_non_standard_method(self, mock_meter, _enabled_mock):
        from ecommerce.metrics.middleware import PrometheusMetricsMiddleware
        request = self.factory.generic('CONNECT', '/')
        mw = PrometheusMetricsMiddleware(_dummy_view(200))
        mw(request)
        mock_meter.http_request.assert_not_called()

    @patch('ecommerce.metrics.middleware.is_metrics_enabled', return_value=True)
    @patch('ecommerce.metrics.middleware.meter')
    def test_records_500_on_exception(self, mock_meter, _enabled_mock):
        from ecommerce.metrics.middleware import PrometheusMetricsMiddleware

        def boom_view(request):
            raise RuntimeError('kaboom')

        request = self.factory.get('/')
        mw = PrometheusMetricsMiddleware(boom_view)
        with self.assertRaises(RuntimeError):
            mw(request)
        args = mock_meter.http_request.call_args
        self.assertEqual(args.args[0], 'GET')
        self.assertEqual(args.args[1], 500)

    @patch('ecommerce.metrics.middleware.is_metrics_enabled', return_value=True)
    @patch('ecommerce.metrics.middleware.meter')
    def test_records_3xx_redirect(self, mock_meter, _enabled_mock):
        from ecommerce.metrics.middleware import PrometheusMetricsMiddleware
        request = self.factory.get('/')
        mw = PrometheusMetricsMiddleware(_dummy_view(302))
        mw(request)
        args = mock_meter.http_request.call_args
        self.assertEqual(args.args[1], 302)

    @patch('ecommerce.metrics.middleware.is_metrics_enabled', return_value=False)
    @patch('ecommerce.metrics.middleware.meter')
    def test_pass_through_when_disabled(self, mock_meter, _enabled_mock):
        """When metrics are off, the meter is never called."""
        from ecommerce.metrics.middleware import PrometheusMetricsMiddleware
        request = self.factory.get('/')
        mw = PrometheusMetricsMiddleware(_dummy_view(200))
        response = mw(request)
        self.assertEqual(response.status_code, 200)
        mock_meter.http_request.assert_not_called()

    @patch('ecommerce.metrics.middleware.is_metrics_enabled', return_value=True)
    @patch('ecommerce.metrics.middleware.meter')
    def test_duration_uses_consistent_clock(self, mock_meter, _enabled_mock):
        """Duration must use the same clock for start and subtraction.

        If ``time.monotonic`` and ``time.perf_counter`` were mixed the duration
        could be negative or wildly wrong. We assert the value is a sane
        non-negative finite float.
        """
        import math
        from ecommerce.metrics.middleware import PrometheusMetricsMiddleware
        request = self.factory.get('/')
        mw = PrometheusMetricsMiddleware(_dummy_view(200))
        mw(request)
        args = mock_meter.http_request.call_args
        duration = args.args[2]
        self.assertIsInstance(duration, float)
        self.assertGreaterEqual(duration, 0.0)
        self.assertTrue(math.isfinite(duration))
