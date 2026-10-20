"""Inventory tracking (card 44ge8hgb).

``decrement_for_order`` is the single entry point: it is called exactly once
per paid order, inside the webhook's guarded ``updated == 1`` branch (see
``payment/views/stripe_webhook_view.py``), so Stripe replays never double
-decrement.

Design (binding, from the card brief):

* Stock is optional per row: ``NULL = untracked`` = always sellable, so
  un-varianted demo data and existing tests are untouched by the migration.
* Decrement on confirmed payment only — never at cart/checkout time, no
  reservation system.
* Never negative: a conditional UPDATE ``WHERE stock >= quantity`` with
  ``F()``; an already-sold-through row is clamped to 0 with a warning, never
  raised.
* Availability flags are NOT auto-flipped — ``available`` stays a merchandising
  decision; stock only feeds the in-stock badge.
* Variant-aware: a line with ``variant`` decrements variant stock; a line
  without decrements product stock.

Batch behaviour: one query per target row is acceptable (orders are small);
no ``select_for_update`` — there is a theoretical under-count window under
concurrent takers, documented here rather than solved.
"""
import logging

from django.db.models import F

logger = logging.getLogger(__name__)


def decrement_for_order(order) -> None:
    """Decrement stock for every line of a paid order, exactly once.

    Builds a per-target quantity map from ``order.items`` (variant FK set ->
    variant target, else product target); skip untracked targets
    (``stock is None``). Conditional updates clamp at 0 and log a warning;
    the clamp path itself never raises, but the caller (the webhook) wraps
    this call so a database error can never skip the invoice dispatch.
    """
    targets = {}  # model class -> {pk: total_qty}
    for item in order.items.select_related('product', 'variant').all():
        target = item.variant if item.variant_id is not None else item.product
        if target is None:
            continue
        if getattr(target, 'stock', None) is None:
            continue  # untracked target: always sellable, nothing to decrement
        targets.setdefault(target.__class__, {}).setdefault(target.pk, 0)
        targets[target.__class__][target.pk] += item.quantity

    for model_class, rows in targets.items():
        for pk, qty in rows.items():
            updated = model_class.objects.filter(
                pk=pk, stock__gte=qty
            ).update(stock=F('stock') - qty)
            if updated == 0:
                # Already sold through (or a concurrent taker won the row):
                # clamp to 0, never negative, and warn so it is visible.
                model_class.objects.filter(pk=pk, stock__gte=0).update(stock=0)
                logger.warning(
                    'inventory sold through for %s pk=%s ordered_qty=%d '
                    '-- clamped to 0 (never negative)',
                    model_class.__name__,
                    pk,
                    qty,
                )
