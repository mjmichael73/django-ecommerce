"""Prometheus HTTP metrics middleware.

Card g37aggbd — Phase 4.1 monitoring.

Records per-request count and latency with bounded labels (HTTP method and
status-group only — never the raw URL to avoid cardinality issues).

When ``PROMETHEUS_ENABLED`` is falsy the middleware is a pass-through: it
calls the view but never touches the metrics meter, so there is zero overhead
in development or when metrics are disabled.

The ``/metrics`` path is excluded so scraping your own exporter doesn't
generate a metric about scraping.
"""
from __future__ import annotations

import time

from ecommerce.metrics.registry import is_metrics_enabled, meter

# Metrics path is excluded from instrumentation to avoid self-referential
# request counts (a scrape would increment the counters it reports).
METRICS_PATH = '/metrics'

# Standard HTTP methods Django may dispatch. Anything outside this set is
# silently ignored to keep label cardinality bounded.
_RECORDABLE_METHODS = frozenset(
    {'GET', 'POST', 'HEAD', 'OPTIONS', 'PUT', 'PATCH', 'DELETE'},
)


class PrometheusMetricsMiddleware:
    """Record HTTP request count + latency to Prometheus.

    Must be *inner* in the middleware stack (after the request-ID and CSP
    middleware) so it wraps only the real view logic, not static-file or
    security-middleware early returns. The ``/metrics`` path is excluded.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Short-circuit the metrics path, non-recordable methods, and the
        # disabled-flag so we don't add latency when metrics are off.
        path = getattr(request, 'path', '')
        if not is_metrics_enabled():
            return self.get_response(request)
        if path == METRICS_PATH or request.method not in _RECORDABLE_METHODS:
            return self.get_response(request)

        start = time.monotonic()
        try:
            response = self.get_response(request)
        except Exception:
            # Even on a 500 raised by the view, record the attempt so
            # error rates are visible. Re-raise after recording.
            duration = time.perf_counter() - start
            meter.http_request(request.method, 500, duration)
            raise
        else:
            duration = time.perf_counter() - start
            meter.http_request(request.method, response.status_code, duration)
            return response
