from django.urls import reverse
from django.shortcuts import render, redirect
from orders.models import OrderItem
from orders.forms import OrderCreateForm
from cart.utils import Cart
from orders.tasks import order_created


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
                    quantity=item['quantity']
                )
            cart.clear()
            order_created.delay(order.id)
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
