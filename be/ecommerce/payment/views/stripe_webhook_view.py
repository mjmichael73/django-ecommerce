import stripe
from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from orders.models import Order
from orders.tasks import payment_completed


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
                # Launch asynchronous task for payment completed
                payment_completed.delay(order.id)
    return HttpResponse(status=200)
