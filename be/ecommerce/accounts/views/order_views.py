from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_http_methods

from ecommerce.rate_limit import rate_limit
from orders.models import Order


@login_required
def order_list(request):
    orders = Order.objects.filter(user=request.user).only(
        'id', 'created', 'paid', 'discount',
    ).prefetch_related(
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
        Order.objects.only(
            'id', 'created', 'paid', 'discount', 'first_name', 'last_name',
            'email', 'address', 'postal_code', 'city', 'stripe_id',
        ).prefetch_related('items', 'items__product'),
        pk=pk,
        user=request.user,
    )
    return render(
        request,
        'accounts/order_detail.html',
        {'order': order},
    )


# Rate-limit POST only; GET renders the form without consuming quota (matches
# the coupon pattern — checkout/coupon/review scopes). The ``tracking`` scope
# must be registered in ecommerce.rate_limit.CODE_DEFAULTS.
@rate_limit('tracking', methods=('POST',))
@require_http_methods(['GET', 'POST'])
def order_track(request):
    """Public order tracking lookup (card ee76485f).

    A customer enters their order number + billing email on a public page;
    the view returns the matching order's public tracking snippet. Anonymous
    users are welcome — no login required — but the lookup is keyed on the
    billing email stored on the order, so only the order owner (or someone
    with the numeric id + the billing email) can view it.

    Security boundary: only *paid* orders are ever exposed. An unpaid (or
    non-existent) order produces the same "not found" result, so neither the
    existence of an order nor its billing email can be enumerated by guessing
    the numeric id.
    """
    order = None
    error = None
    if request.method == 'POST':
        order_id = request.POST.get('order_id', '').strip()
        email = request.POST.get('email', '').strip().lower()
        try:
            order_id_int = int(order_id)
        except (TypeError, ValueError):
            order_id_int = None
            error = 'Please enter a valid order number.'
        if order_id_int is not None:
            # Only expose a paid order: an unpaid order has not been charged
            # yet and must never leak its existence. 404-equivalent keeps the
            # lookup result count at zero for both "no such order" and
            # "order exists but unpaid or email mismatch" — no enumeration.
            order = Order.objects.filter(
                pk=order_id_int,
                paid=True,
                email__iexact=email,
            ).first()
            if not order:
                error = 'No paid order found matching that number and email.'
    return render(
        request,
        'accounts/order_track.html',
        {'order': order, 'error': error},
    )
