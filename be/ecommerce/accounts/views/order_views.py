from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render

from orders.models import Order


@login_required
def order_list(request):
    orders = Order.objects.filter(user=request.user).prefetch_related(
        'items',
    )
    return render(
        request,
        'accounts/order_list.html',
        {'orders': orders},
    )


@login_required
def order_detail(request, pk):
    order = get_object_or_404(
        Order.objects.prefetch_related('items', 'items__product'),
        pk=pk,
        user=request.user,
    )
    return render(
        request,
        'accounts/order_detail.html',
        {'order': order},
    )
