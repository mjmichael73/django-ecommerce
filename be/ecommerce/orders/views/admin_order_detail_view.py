from django.shortcuts import render, get_object_or_404
from django.contrib.admin.views.decorators import staff_member_required
from orders.models import Order


@staff_member_required
def admin_order_detail(request, order_id):
    order = get_object_or_404(
        Order.objects.prefetch_related('items', 'items__product'),
        id=order_id,
    )
    return render(
        request,
        'admin/orders/order/detail.html',
        {
            'order': order
        }
    )
