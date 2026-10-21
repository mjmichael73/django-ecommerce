import logging
from decimal import Decimal

import stripe
from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from orders.fraud import evaluate, flag_threshold
from orders.models import Order
from orders.tasks import payment_completed
from shop import inventory
from ecommerce.middleware.request_id import propagate_request_id
from ecommerce.metrics.business import record_order_paid

logger = logging.getLogger(__name__)


@csrf_exempt
def stripe_webhook(request):
    payload = request.body
    sig_header = request.META.get('HTTP_STRIPE_SIGNATURE')
    if not sig_header:
        return HttpResponse(status=400)
    event = None
    try:
        event = stripe.Webhook.construct_event(
            payload,
            sig_header,
            settings.STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        return HttpResponse(status=400)
    except stripe.error.SignatureVerificationError:
        return HttpResponse(status=400)
    if event.type == "checkout.session.completed":
        session = event.data.object
        if session.mode == "payment" and session.payment_status == "paid":
            try:
                order_id = int(session.client_reference_id)
            except (TypeError, ValueError):
                return HttpResponse(status=400)
            try:
                order = Order.objects.get(id=order_id)
            except Order.DoesNotExist:
                return HttpResponse(status=400)
            # Idempotency: Stripe delivers webhooks at-least-once, so replays
            # and concurrent duplicates must be no-ops. The guarded UPDATE
            # flips paid only once (first delivery wins) and never rewrites
            # the stripe_id recorded by that first payment.
            # Idempotency: Stripe delivers webhooks at-least-once, so replays
            # and concurrent duplicates must be no-ops. The guarded UPDATE
            # flips paid only once (first delivery wins) and never rewrites
            # the stripe_id recorded by that first payment. The status is
            # advanced to PAID by the same atomic guard so a replay never
            # re-transitions it.
            updated = Order.objects.filter(
                id=order_id, paid=False,
            ).update(
                paid=True,
                stripe_id=session.payment_intent,
                status=Order.Status.PAID,
            )
            if updated == 1:
                # Business metric: order paid (card g37aggbd). Record the
                # revenue at the moment of payment.
                try:
                    record_order_paid(float(order.get_total_cost()))
                except Exception:
                    logger.exception(
                        'failed to record order_paid metric for order %s',
                        order_id,
                    )
                # Fraud evaluation (card 832hc4eg) — FLAG-ONLY, BEST-EFFORT.
                # It scores the order and persists the signals; it never
                # un-pays, blocks, or 40x's anything (the row is already paid
                # above). It runs here because the guarded update above makes
                # this branch run exactly once per order (idempotency gate),
                # so replays never re-evaluate. Evaluation happens AFTER the
                # payment fields landed; update() (not .save()) never stomps
                # concurrent changes to other Order fields.
                #
                # Error-handling decision: evaluate + persist run in ONE
                # try/except — a failure inside it (evaluation or the
                # persist UPDATE itself) is caught and logged only. The
                # invoice dispatch below is UNCONDITIONAL inside this
                # branch, not tied to the fraud outcome: paid=True has
                # already committed, so a persist crash that skipped the
                # invoice would be unrecoverable — Stripe's replay answers
                # updated == 0 and never reaches this code again.
                try:
                    score, flags = evaluate(order)
                    Order.objects.filter(id=order_id).update(
                        fraud_score=score,
                        is_flagged=score >= flag_threshold(),
                        fraud_notes=','.join(flags),
                    )
                except Exception:
                    logger.exception(
                        'fraud evaluation/persistence failed for order %s',
                        order_id,
                    )
                # Decrement stock once, guarded by the same idempotency gate
                # above (replays never reach this branch again). Wrapped so a
                # database hiccup here can never skip the invoice dispatch:
                # payment_completed.delay always runs, matching how the fraud
                # block treats its own failures. Placed before `.delay` so a
                # DB hiccup is visible rather than silently swallowed.
                try:
                    inventory.decrement_for_order(order)
                except Exception:
                    logger.exception(
                        'inventory decrement failed for order %s', order_id,
                    )
                # Launch asynchronous task for payment completed —
                # unconditional inside the idempotency gate, above. The active
                # request ID is forwarded so the task's log lines carry it
                # for end-to-end correlation (card 6cc44f8g).
                payment_completed.delay(order.id, **propagate_request_id())
    elif event.type == 'charge.refunded':
        # Inbound truth for refunds (card a3cg42c4). A refund issued in the
        # Stripe dashboard (or anywhere else outside this app) arrives here,
        # so the local order reflects it. The Stripe charge carries the
        # cumulative refunded sum, so the amount is written as-is (not
        # incremented). No invoice re-dispatch, no fraud evaluation — a
        # refund is not a payment event.
        _apply_refunded_event(event.data.object)
    return HttpResponse(status=200)


def _single_refund_id(charge) -> str:
    """Return the Stripe Refund id of a charge that was refunded exactly
    once; blank when several refunds happened (a single id cannot attribute
    the refunded amount unambiguously) or when the event carries no refund
    list at all."""
    refunds = getattr(charge, 'refunds', None)
    data = getattr(refunds, 'data', None)
    if not data or len(data) > 1:
        return ''
    return getattr(data[0], 'id', '') or ''


def _apply_refunded_event(charge) -> None:
    """Record an inbound ``charge.refunded`` event on the matching order."""
    payment_intent = getattr(charge, 'payment_intent', None)
    amount_refunded = getattr(charge, 'amount_refunded', None)
    if not payment_intent or amount_refunded is None:
        return
    try:
        # Stripe speaks cents, the order stores $ units.
        refunded_amount = Decimal(str(amount_refunded)) / Decimal('100')
    except (TypeError, ValueError, ArithmeticError):
        logger.exception('unusable amount_refunded in charge.refunded event')
        return
    order = Order.objects.filter(stripe_id=payment_intent).first()
    if order is None:
        # Refund for a payment we do not know about — nothing to update.
        return
    Order.objects.filter(pk=order.pk).update(
        refunded_amount=refunded_amount,
        refund_id=_single_refund_id(charge),
        refunded_at=timezone.now(),
    )
