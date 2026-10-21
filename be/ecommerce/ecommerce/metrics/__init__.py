"""Prometheus metrics for the Django e-commerce application.

Card g37aggbd — Phase 4.1 monitoring.

This package provides optional Prometheus metrics that are only activated when
``PROMETHEUS_ENABLED`` is set in the environment. When disabled, the metrics
object is a no-op so normal startup and request handling are never affected.

Modules:
    registry — the prometheus Client registry + metric definitions with
        bounded labels and cardinality guards.
    middleware — ``PrometheusMetricsMiddleware`` that records HTTP
        request count/latency/status, skipped for the ``/metrics`` path.
    business — business-level metric helpers (orders created, paid, refunds).
"""
