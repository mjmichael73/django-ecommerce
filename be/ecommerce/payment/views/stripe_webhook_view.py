import logging

import stripe
from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from orders.fraud import evaluate, flag_threshold
from orders.models import Order
from orders.tasks import payment_completed

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
            updated = Order.objects.filter(id=order_id, paid=False).update(
                paid=True,
                stripe_id=session.payment_intent,
            )
            if updated == 1:
                # Fraud evaluation (card 832hc4eg) — FLAG-ONLY. It scores the
                # order and persists the signals; it never un-pays, blocks,
                # or 40x's anything (the row is already paid above). It runs
                # here because the guarded update above makes this branch run
                # exactly once per order (idempotency gate), so replays never
                # re-evaluate. Evaluation happens AFTER the payment fields
                # landed. Evaluating via update() (not .save()) never stomps
                # concurrent changes to other Order fields.
                #
                # Error-handling decision: evaluation failure is caught and
                # logged only. The invoice dispatch below runs REGARDLESS of
                # the fraud outcome — the customer's invoice is the primary
                # side effect of payment, and a fraud-signal problem must
                # never delay or drop it (same best-effort style as the mail
                # task, which the view does not defend against either).
                try:
                    score, flags = evaluate(order)
                except Exception:
                    # Unreachable as long as every rule guards itself, but a
                    # failure here must never fail the webhook or leave the
                    # order stuck: paid=True was already committed.
                    logger.exception(
                        'fraud evaluation failed for order %s', order_id
                    )
                else:
                    Order.objects.filter(id=order_id).update(
                        fraud_score=score,
                        is_flagged=score >= flag_threshold(),
                        fraud_notes=','.join(flags),
                    )
                # Launch asynchronous task for payment completed
                payment_completed.delay(order.id)
    return HttpResponse(status=200)
