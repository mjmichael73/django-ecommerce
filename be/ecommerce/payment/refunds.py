"""Refund primitives for paid orders (card a3cg42c4).

This module is the only place that talks to the Stripe Refund API. It is
deliberately *not* a customer-facing flow — the customer-facing return /
refund request workflow is a separate card (4fcgc9e). Nothing here is
triggered automatically: refunds are analyst-initiated through the
``issue_full_refund`` admin action, or they are observed afterwards through
inbound Stripe ``charge.refunded`` webhook events (handled in
``payment/views/stripe_webhook_view.py``).

Design notes
------------

*Why ``update()`` and not ``save()``*: the refund fields are written with a
single ``Order.objects.filter(pk=...).update(...)``. The order row is touched
by several concurrent flows (the webhook that marks it paid, the fraud
engine, the invoice task). ``.save()`` would rewrite *every* field of the
in-memory instance and could stomp a concurrent change that landed after the
instance was loaded (e.g. ``stripe_id`` written by the payment webhook).
A targeted ``UPDATE`` touches only the refund columns — the same write
discipline the payment-idempotency and fraud cards use.

*Cents conversion*: amounts are handled in **$ units** everywhere in this
project (``OrderItem.price``, ``Order.get_total_cost()``). Stripe speaks
**cents** (integers), so a partial refund of ``Decimal('10.50')`` is sent as
``int(amount * Decimal('100'))`` == ``1050`` — the same conversion
``payment_process`` uses for line items. The full-refund call deliberately
omits the amount, letting Stripe refund the whole remaining charge.

*Errors are values, not exceptions*: a Stripe failure must never surface as a
500. Every error is caught, logged and returned as ``(False, reason)`` so the
caller (admin action, or any future caller) can show a message instead of
crashing.
"""

import logging
from decimal import Decimal

import stripe
from django.utils import timezone

from orders.models import Order

from ecommerce.metrics.business import record_order_refunded

logger = logging.getLogger(__name__)

# Reasons returned by refund_order() — stable strings, the admin action
# shows them to the analyst verbatim.
REASON_NOT_REFUNDABLE = 'not refundable'
REASON_INVALID_AMOUNT = 'invalid amount'
REASON_ALREADY_REFUNDED = 'already refunded'
REASON_OK = 'ok'


def _cents(amount) -> int:
    """Convert a $ amount to integer cents the way Stripe expects it."""
    return int(amount * Decimal('100'))


def _persisted_refunded_amount(order) -> Decimal:
    """Return the refunded amount as currently stored in the database.

    The in-memory instance may be stale (the same object can be refunded
    twice in one process — e.g. a double-clicked admin action), so the
    guard below reads the persisted value and only falls back to the
    instance attribute when the row cannot be read.
    """
    persisted = (
        Order.objects.filter(pk=order.pk)
        .values_list('refunded_amount', flat=True)
        .first()
    )
    if persisted is None:
        return order.refunded_amount or Decimal('0')
    return persisted


def refund_order(order, amount=None) -> tuple[bool, str]:
    """Refund *order* in Stripe and record the result on the order row.

    ``amount`` is a ``Decimal`` in $ units; ``None`` means "refund the full
    remaining total". Returns ``(True, 'ok')`` on success and
    ``(False, reason)`` otherwise — this function never raises and never
    lets a Stripe error escape into a view.
    """
    # Guard 1: only a paid order that carries a Stripe payment_intent can
    # be refunded — no Stripe call at all otherwise.
    if not order.paid or not order.stripe_id:
        return False, REASON_NOT_REFUNDABLE

    # Guard 2: a partial refund must have a positive amount. The amount is
    # coerced to Decimal first so a float or a string can never blow up the
    # cents conversion below.
    if amount is not None:
        try:
            amount = Decimal(str(amount))
        except (TypeError, ValueError, ArithmeticError):
            return False, REASON_INVALID_AMOUNT
        if amount <= 0:
            return False, REASON_INVALID_AMOUNT

    refunded_so_far = _persisted_refunded_amount(order)
    total_cost = order.get_total_cost()

    # Guard 3: local double-refund protection. If everything has already
    # been refunded locally we refuse *before* calling Stripe, so a
    # double-clicked admin action cannot refund twice. (An order without
    # items has a zero total and is refused here as well — there is nothing
    # to refund.)
    if refunded_so_far >= total_cost:
        return False, REASON_ALREADY_REFUNDED

    try:
        if amount is None:
            # Full refund — Stripe refunds the whole remaining charge.
            refund = stripe.Refund.create(payment_intent=order.stripe_id)
            refunded_delta = total_cost - refunded_so_far
        else:
            # Partial refund — Stripe wants integer cents.
            refund = stripe.Refund.create(
                payment_intent=order.stripe_id,
                amount=_cents(amount),
            )
            refunded_delta = amount
    except stripe.error.StripeError as exc:
        # CardError and its relatives are StripeError subclasses; nothing
        # here is ever re-raised into a view.
        logger.exception('stripe refund failed for order %s', order.pk)
        return False, f'stripe error: {exc}'
    except Exception as exc:
        logger.exception('unexpected error during refund of order %s', order.pk)
        return False, f'stripe error: {exc}'

    refund_id = getattr(refund, 'id', '') or ''
    refunded_at = timezone.now()
    new_refunded_amount = refunded_so_far + refunded_delta

    # Targeted UPDATE (never .save()) — see the module docstring.
    Order.objects.filter(pk=order.pk).update(
        refunded_amount=new_refunded_amount,
        refund_id=refund_id,
        refunded_at=refunded_at,
    )

    # Keep the in-memory instance in sync so a second call with the same
    # object sees the new state (and hits the "already refunded" guard).
    order.refunded_amount = new_refunded_amount
    order.refund_id = refund_id
    order.refunded_at = refunded_at

    # Business metric (card g37aggbd) — record the refunded amount.
    try:
        record_order_refunded(order.pk, float(refunded_delta))
    except Exception:
        logger.exception('failed to record refund metric for order %s', order.pk)

    return True, REASON_OK
