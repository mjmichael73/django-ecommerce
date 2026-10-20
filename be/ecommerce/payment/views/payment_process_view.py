from decimal import Decimal
import stripe
from django.conf import settings
from django.shortcuts import render, redirect, reverse, get_object_or_404
from orders.models import Order

stripe.api_key = settings.STRIPE_SECRET_KEY


def payment_process(request):
    order_id = request.session.get('order_id', None)
    order = get_object_or_404(Order, id=order_id)
    # A paid order must never be sent through Stripe again — the session
    # outlives the webhook (session['order_id'] survives), so a returning
    # user gets the completed page instead of a second checkout session.
    if order.paid:
        return redirect(reverse('payment:completed'))
    if request.method == "POST":
        success_url = request.build_absolute_uri(
            reverse('payment:completed')
        )
        cancel_url = request.build_absolute_uri(
            reverse('payment:canceled')
        )
        session_data = {
            'mode': 'payment',
            'client_reference_id': order.id,
            'success_url': success_url,
            'cancel_url': cancel_url,
            'line_items': []
        }
        for item in order.items.all():
            session_data['line_items'].append(
                {
                    'price_data': {
                        'unit_amount': int(item.price * Decimal('100')),
                        'currency': 'usd',
                        'product_data': {
                            'name': item.product.name,
                        }
                    },
                    'quantity': item.quantity,
                }
            )
        # Idempotency key is deterministic per order; Stripe dedups identical
        # session creations under it for 24h, so a crash/retry here can never
        # produce two checkout sessions (and thus two charges) for one order.
        session = stripe.checkout.Session.create(
            **session_data,
            idempotency_key=f'order-{order.id}',
        )
        return redirect(session.url, code=302)
    else:
        return render(request, 'payment/process.html', locals())
