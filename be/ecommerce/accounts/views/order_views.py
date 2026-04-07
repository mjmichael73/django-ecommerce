from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render

from orders.models import Order


@login_required
def order_list(request):
    orders = Order.objects.filter(user=request.user)
    return render(
        request,
        'accounts/order_list.html',
        {'orders': orders},
    )


@login_required
def order_detail(request, pk):
    order = get_object_or_404(Order, pk=pk, user=request.user)
    return render(
        request,
        'accounts/order_detail.html',
        {'order': order},
    )
