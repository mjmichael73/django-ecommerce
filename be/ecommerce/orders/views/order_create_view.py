from django.urls import reverse
from django.shortcuts import render, redirect
from ecommerce.rate_limit import rate_limit
from ecommerce.middleware.request_id import propagate_request_id
from ecommerce.metrics.business import record_order_created
from orders.models import OrderItem
from orders.forms import OrderCreateForm
from cart.utils import Cart
from orders.tasks import order_created


@rate_limit('checkout')
def order_create(request):
    cart = Cart(request)
    if request.method == 'POST':
        form = OrderCreateForm(request.POST)
        if form.is_valid():
            order = form.save(commit=False)
            if request.user.is_authenticated:
                order.user = request.user
            order.save()
            for item in cart:
                OrderItem.objects.create(
                    order=order,
                    product=item['product'],
                    price=item['price'],
                    quantity=item['quantity'],
                    # Use the iter-attached instance (None when the variant row
                    # was deleted after the line was added) — never the raw
                    # variant_id, which would be a dead FK and raise here.
                    variant=item.get('variant'),
                )
            cart.clear()
            # Business metric: order created (card g37aggbd). Record the
            # initial status and gross total.
            record_order_created(order.status, float(order.get_total_cost_before_discount()))
            # Forward the active request ID so the invoice/email task logs stay
            # correlated with this checkout request (card 6cc44f8g).
            order_created.delay(order.id, **propagate_request_id())
            request.session['order_id'] = order.id
            return redirect(reverse('payment:process'))
    else:
        initial = {}
        if request.user.is_authenticated:
            u = request.user
            initial = {
                'first_name': u.first_name or '',
                'last_name': u.last_name or '',
                'email': u.email or '',
            }
        form = OrderCreateForm(initial=initial)
    return render(
        request,
        "orders/order/create.html",
        {
            "cart": cart,
            "form": form,
        }
    )
