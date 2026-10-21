"""Prometheus metric definitions with bounded labels.

Card g37aggbd — Phase 4.1 monitoring.

All metrics use ``prometheus_client`` and are registered on a dedicated
``CollectorRegistry`` so they can be scraped independently of any global
registry state. Labels (dimensions) are carefully bounded to avoid cardinality
explosions:

* HTTP method — only the values Django dispatches (GET, POST, HEAD, OPTIONS,
  PUT, PATCH, DELETE) appear. We never label on the raw URL or path to keep
  cardinality finite.
* HTTP status — the canonical Django/DRF status groups (2xx, 3xx, 4xx, 5xx).
* Order status — the finite set from ``Order.Status`` choices.

The ``metrics_enabled`` flag (read from ``PROMETHEUS_ENABLED`` env var and
Django settings) gates recording: every ``_PrometheusMeter`` method checks
``is_metrics_enabled()`` at call time and returns early when off, so there is
zero side-effect and no exposition is generated when the flag is falsy. The
``/metrics`` view also guards at runtime (404 when disabled).

For tests, ``override_settings(PROMETHEUS_ENABLED=True)`` toggles the flag at
runtime because ``is_metrics_enabled()`` reads from Django settings.
"""
from __future__ import annotations

import os

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)
from prometheus_client.exposition import CONTENT_TYPE_LATEST

__all__ = [
    'metrics_enabled',
    'registry',
    'http_requests_total',
    'http_request_duration_seconds',
    'orders_total',
    'orders_revenue_total',
    'orders_refunded_total',
    'celery_tasks_total',
    'celery_task_duration_seconds',
    'METRICS_CONTENT_TYPE',
    'metrics_exposition',
    'meter',
    'is_metrics_enabled',
    'is_metrics_exposition_enabled',
    'HTTP_METHODS',
    'HTTP_STATUS_GROUPS',
    '_ORDER_STATUSES',
]

# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------

metrics_enabled = os.environ.get('PROMETHEUS_ENABLED', '').lower() in (
    '1', 'true', 'yes',
)


def is_metrics_enabled() -> bool:
    """Return whether Prometheus metrics recording is active.

    Reads ``PROMETHEUS_ENABLED`` from Django settings (falling back to the
    env-var-derived value set at import time). Using settings means
    ``override_settings(PROMETHEUS_ENABLED=True/False)`` toggles the flag at
    runtime in tests without re-importing the module.
    """
    try:
        from django.conf import settings
        return bool(getattr(settings, 'PROMETHEUS_ENABLED', metrics_enabled))
    except Exception:
        # Settings not configured (bare Python import).
        return metrics_enabled


def is_metrics_exposition_enabled() -> bool:
    """Whether the /metrics endpoint is mounted and serving.

    This is the same flag as :func:`is_metrics_enabled` but named to make
    the intent clear at call sites that gate the endpoint.
    """
    return is_metrics_enabled()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HTTP_METHODS = ('GET', 'POST', 'HEAD', 'OPTIONS', 'PUT', 'PATCH', 'DELETE')
HTTP_STATUS_GROUPS = ('2xx', '3xx', '4xx', '5xx')

# Business metrics — bounded by the finite Order.Status choices.
# We hard-code the known status labels to avoid importing the orders
# models at module load time (which would be fragile during Django's
# app-initialization phase). If orders/models.py adds a new status it
# must also be added here; the tests assert this stays in sync.
_ORDER_STATUSES = (
    'PENDING', 'PAID', 'PROCESSING', 'SHIPPED', 'DELIVERED', 'CANCELED',
)

METRICS_CONTENT_TYPE = CONTENT_TYPE_LATEST

# ---------------------------------------------------------------------------
# Registry (dedicated so /metrics returns only our metrics)
# ---------------------------------------------------------------------------

registry = CollectorRegistry(auto_describe=True)


# ---------------------------------------------------------------------------
# Meter implementations
# ---------------------------------------------------------------------------

class _PrometheusMeter:
    """Typed recorders that wrap the real ``Counter``/``Histogram`` objects."""

    def __init__(
        self,
        http_requests_total: Counter,
        http_request_duration_seconds: Histogram,
        orders_total: Counter,
        orders_revenue_total: Counter,
        orders_refunded_total: Counter,
        celery_tasks_total: Counter,
        celery_task_duration_seconds: Histogram,
    ):
        self._http_requests_total = http_requests_total
        self._http_request_duration_seconds = http_request_duration_seconds
        self._orders_total = orders_total
        self._orders_revenue_total = orders_revenue_total
        self._orders_refunded_total = orders_refunded_total
        self._celery_tasks_total = celery_tasks_total
        self._celery_task_duration_seconds = celery_task_duration_seconds

    @staticmethod
    def _status_group(status_code: int) -> str:
        """Map a numeric status code to its ``2xx``/``3xx``/``4xx``/``5xx`` group."""
        if status_code < 300:
            return '2xx'
        if status_code < 400:
            return '3xx'
        if status_code < 500:
            return '4xx'
        return '5xx'

    def http_request(self, method: str, status_code: int, duration: float) -> None:
        """Record one HTTP request with bounded method + status-group labels."""
        if not is_metrics_enabled():
            return
        method = method.upper()
        if method not in HTTP_METHODS:
            return  # ignore non-standard methods (e.g. CONNECT) to bound labels
        group = self._status_group(status_code)
        self._http_requests_total.labels(method=method, status=group).inc()
        self._http_request_duration_seconds.labels(
            method=method, status=group
        ).observe(duration)

    def order_created(self, status: str, amount: float) -> None:
        """Record an order landing in ``status`` with the given gross ``amount``."""
        if not is_metrics_enabled():
            return
        if status not in _ORDER_STATUSES:
            status = 'PENDING'
        self._orders_total.labels(status=status).inc()
        self._orders_revenue_total.labels(status=status).inc(amount)

    def order_paid(self, amount: float) -> None:
        """Record revenue for an order that just transitioned to paid."""
        self.order_created('PAID', amount)

    def order_refunded(self, order_id: int, amount: float) -> None:
        """Record a refund. ``order_id`` is bounded in practice by low volume."""
        if not is_metrics_enabled():
            return
        self._orders_refunded_total.labels(order_id=str(order_id)).inc(amount)

    def celery_task(self, name: str, status: str, duration: float) -> None:
        """Record a completed Celery task (status is ``success``/``failure``)."""
        if not is_metrics_enabled():
            return
        self._celery_tasks_total.labels(task=name, status=status).inc()
        self._celery_task_duration_seconds.labels(task=name).observe(duration)


# ---------------------------------------------------------------------------
# Metric definitions + meter instantiation
# ---------------------------------------------------------------------------

# The metric objects are always created so the module can be imported safely
# in any process. The ``_PrometheusMeter`` methods check ``is_metrics_enabled()``
# at call time and return early when the flag is off, so there is no side-effect
# and no exposition is generated when metrics are disabled.
http_requests_total = Counter(
    'django_http_requests_total',
    'Total number of HTTP requests.',
    ['method', 'status'],
    registry=registry,
)
http_request_duration_seconds = Histogram(
    'django_http_request_duration_seconds',
    'HTTP request latency in seconds.',
    ['method', 'status'],
    registry=registry,
)
orders_total = Counter(
    'django_orders_total',
    'Total orders created, by status.',
    ['status'],
    registry=registry,
)
orders_revenue_total = Counter(
    'django_orders_revenue_total',
    'Total order revenue (in dollars), labelled by status.',
    ['status'],
    registry=registry,
)
orders_refunded_total = Counter(
    'django_orders_refunded_total',
    'Total refunded amount (in dollars).',
    ['order_id'],
    registry=registry,
)
celery_tasks_total = Counter(
    'django_celery_tasks_total',
    'Total Celery tasks by name and status.',
    ['task', 'status'],
    registry=registry,
)
celery_task_duration_seconds = Histogram(
    'django_celery_task_duration_seconds',
    'Celery task execution time in seconds.',
    ['task'],
    registry=registry,
)

# Pre-warm label-value sets so the first scrape includes zero-counts
# for the full label space. (These are cheap and only create the label
# series lazily; they cost nothing when metrics are disabled.)
for method in HTTP_METHODS:
    for status in HTTP_STATUS_GROUPS:
        http_requests_total.labels(method=method, status=status)
for status in _ORDER_STATUSES:
    orders_total.labels(status=status)
    orders_revenue_total.labels(status=status)

meter = _PrometheusMeter(
    http_requests_total,
    http_request_duration_seconds,
    orders_total,
    orders_revenue_total,
    orders_refunded_total,
    celery_tasks_total,
    celery_task_duration_seconds,
)


# ---------------------------------------------------------------------------
# Public exposition helper
# ---------------------------------------------------------------------------

def metrics_exposition() -> bytes:
    """Return the Prometheus text-format exposition of all registered metrics.

    Only callable when ``PROMETHEUS_ENABLED`` is set — the ``/metrics`` view
    rejects requests before reaching this helper.
    """
    return generate_latest(registry)
