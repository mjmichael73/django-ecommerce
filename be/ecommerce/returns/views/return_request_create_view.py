from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from ecommerce.rate_limit import rate_limit
from orders.models import Order
from returns.forms import ReturnRequestForm


@login_required
@rate_limit('return', methods=('POST',))
@require_http_methods(['GET', 'POST'])
def return_request_create(request, pk):
    """Submit a return request for one of the customer's paid orders.

    Security boundary:
    * ``@login_required`` — only authenticated customers can request returns.
    * The order is fetched filtered by ``user=request.user`` AND ``paid=True``,
      so a customer can only return orders they actually own *and* that have
      been charged. An unpaid or someone-else's order returns 404 (no leak).
    * The POST is rate-limited via the ``return`` scope (card 4fcgcg9e).

    The request is recorded as ``SUBMITTED``; no refund is issued at this point.
    A real refund only happens later, when an analyst approves and then
    explicitly refunds via the admin.
    """
    order = get_object_or_404(
        Order,
        pk=pk,
        user=request.user,
        paid=True,
    )
    form = ReturnRequestForm(request.POST or None, order=order)
    if request.method == 'POST' and form.is_valid():
        rr = form.save(commit=False)
        rr.user = request.user
        rr.save()
        messages.success(
            request,
            'Your return request has been submitted and is pending review.',
        )
        return redirect('accounts:return_list')
    return render(
        request,
        'accounts/return_request_form.html',
        {'form': form, 'order': order},
    )
