from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def return_request_list(request):
    """List the customer's own return requests.

    Only the requesting user's requests are shown — no enumeration of other
    users' orders or returns is possible.
    """
    return_requests = (
        request.user.return_requests
        .select_related('order')
        .order_by('-created')
    )
    return render(
        request,
        'accounts/return_list.html',
        {'return_requests': return_requests},
    )
