"""Business-level metric helpers.

Card g37aggbd — Phase 4.1 monitoring.

Thin wrappers around the active ``meter`` so views and tasks record business
events (orders, payments, refunds) without importing prometheus_client
directly. When metrics are disabled the meter is a no-op, so these calls
are free.

Each public function is deliberately tiny — it exists so domain code reads:
``record_order_created(order.status, total)`` instead of importing a metrics
module and knowing the label names.
"""
from __future__ import annotations

from ecommerce.metrics.registry import meter

__all__ = [
    'record_order_created',
    'record_order_paid',
    'record_order_refunded',
    'record_celery_task',
]


def record_order_created(status: str, amount: float) -> None:
    """Record an order creation event (labelled by order status)."""
    meter.order_created(status, float(amount))


def record_order_paid(amount: float) -> None:
    """Record revenue for an order that just became paid."""
    meter.order_paid(float(amount))


def record_order_refunded(order_id: int, amount: float) -> None:
    """Record a refund against a specific order."""
    meter.order_refunded(order_id, float(amount))


def record_celery_task(name: str, status: str, duration: float) -> None:
    """Record a completed Celery task.

    ``status`` should be ``'success'`` or ``'failure'``.
    """
    meter.celery_task(name, status, float(duration))
