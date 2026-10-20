"""Fraud detection for orders — card 832hc4eg.

Flag-only by design: this engine DETECTS suspicious orders and returns a
numeric score plus the list of rule names that fired. It never blocks a
payment, cancels a charge, or refuses an order — auto-blocking is a product
decision this module does not make. The score/notes are persisted onto the
Order (``fraud_score``, ``is_flagged``, ``fraud_notes``) and reviewed by an
analyst through the admin action described there.

Rules are deterministic, pure-ORM (no Stripe APIs, no extra dependencies)
and run once per order, inside the webhook's guarded ``updated == 1``
payment transition — the idempotency gate from card 7e732hbh guarantees
exactly one evaluation per order.

New rules are append-only: add a check function and a tuple to ``RULES``.
Every rule must guard its FK-less cases (no user, no coupon) and never
raise; a rule failure is logged and treated as "did not fire".

Weights and thresholds are Django settings read at call time (same pattern
as card 25ahc7hg's rate limits), so operators and tests can override them:

    FRAUD_FLAG_THRESHOLD = 40             # score at/above this flags the order
    FRAUD_WINDOW_HOURS = 24               # velocity/coupon window (UTC hours)
    FRAUD_EMAIL_VELOCITY = 3              # sibling orders sharing the email
    FRAUD_LARGE_TOTAL = 1000              # order total in USD (Decimal)
    FRAUD_BULK_QUANTITY = 50              # total item quantity
    FRAUD_NEW_ACCOUNT_HOURS = 24          # account age window
    FRAUD_NEW_ACCOUNT_TOTAL = 300         # new-account order total threshold
    FRAUD_COUPON_VELOCITY = 3             # sibling orders sharing the coupon

    FRAUD_WEIGHT_<RULE_NAME_UPPERCASE>    # per-rule weight override
"""
from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from orders.models import Order

logger = logging.getLogger(__name__)

FLAG_THRESHOLD_DEFAULT = 40


def _int_setting(key: str, default: int) -> int:
    return int(getattr(settings, key, default))


def _threshold(key: str, default: Decimal) -> Decimal:
    return Decimal(str(getattr(settings, key, default)))


def flag_threshold() -> int:
    """Score at/above which an order is considered flagged for review."""
    return _int_setting('FRAUD_FLAG_THRESHOLD', FLAG_THRESHOLD_DEFAULT)


def _window_start(now, hours: int):
    return now - timedelta(hours=hours)


def _rule_weight(name: str, default_weight: int) -> int:
    """Per-rule weight, overridable as ``FRAUD_WEIGHT_<RULE>`` in settings."""
    return _int_setting(f'FRAUD_WEIGHT_{name.upper()}', default_weight)


# --- Rule checks (one function per rule; guards required, never raise) ------

def _velocity_email(order, now) -> bool:
    """Many sibling orders sharing this email within the window."""
    if not order.email:
        return False
    siblings = Order.objects.filter(
        email__iexact=order.email,
        created__gte=_window_start(
            now, _int_setting('FRAUD_WINDOW_HOURS', 24)
        ),
        created__lte=now,
    ).exclude(id=order.id)
    return siblings.count() >= _int_setting('FRAUD_EMAIL_VELOCITY', 3)


def _large_total(order, now) -> bool:
    """Order total (after discount, in USD units) at/above the threshold."""
    return order.get_total_cost() >= _threshold('FRAUD_LARGE_TOTAL', 1000)


def _bulk_quantity(order, now) -> bool:
    """Total item quantity across the order at/above the threshold."""
    total = order.items.aggregate(total=Sum('quantity'))['total']
    return total is not None and total >= _int_setting(
        'FRAUD_BULK_QUANTITY', 50
    )


def _new_account_high_value(order, now) -> bool:
    """Fresh account's first (or early) high-value order."""
    if order.user_id is None or order.user is None:
        return False
    joined_recently = order.user.date_joined >= _window_start(
        now, _int_setting('FRAUD_NEW_ACCOUNT_HOURS', 24)
    )
    return (
        joined_recently
        and order.get_total_cost() >= _threshold(
            'FRAUD_NEW_ACCOUNT_TOTAL', 300
        )
    )


def _identity_mismatch(order, now) -> bool:
    """Checkout form email differs from the account email."""
    if order.user_id is None or order.user is None:
        return False
    return order.email.lower() != (order.user.email or '').lower()


def _coupon_velocity(order, now) -> bool:
    """Many sibling orders reusing the same coupon within the window."""
    if order.coupon_id is None or order.coupon is None:
        return False
    siblings = Order.objects.filter(
        coupon_id=order.coupon_id,
        created__gte=_window_start(
            now, _int_setting('FRAUD_WINDOW_HOURS', 24)
        ),
        created__lte=now,
    ).exclude(id=order.id)
    return siblings.count() >= _int_setting(
        'FRAUD_COUPON_VELOCITY', 3
    )


# Registry: (rule_name, default_weight, check). Append new rules last;
# thresholds/keys they read are documented in the module docstring.
RULES = [
    ('velocity_email', 40, _velocity_email),
    ('large_total', 30, _large_total),
    ('bulk_quantity', 20, _bulk_quantity),
    ('new_account_high_value', 30, _new_account_high_value),
    ('identity_mismatch', 15, _identity_mismatch),
    ('coupon_velocity', 25, _coupon_velocity),
]


def evaluate(order) -> tuple[int, list[str]]:
    """Score *order* for fraud. Returns ``(score, flags)``.

    ``score`` is the sum of the fired rules' weights, capped at 100.
    ``flags`` is a list of fired rule names, in registry order — it becomes
    ``Order.fraud_notes`` (comma-joined).

    Every rule is wrapped in its own try/except: a broken rule (or a decode
    error on odd data) degrades to "did not fire" and is logged, so fraud
    evaluation can never crash a payment flow.
    """
    now = timezone.now()
    score = 0
    flags: list[str] = []
    for name, default_weight, check in RULES:
        try:
            fired = check(order, now)
        except Exception:
            logger.exception('fraud rule %s raised on order %s', name, order.pk)
            fired = False
        if fired:
            flags.append(name)
            score = min(100, score + _rule_weight(name, default_weight))
    return score, flags
