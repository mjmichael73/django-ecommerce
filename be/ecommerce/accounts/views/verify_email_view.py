from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse

from accounts.verification import verify


def verify_email(request, token_secret):
    """Confirm the email address linked to *token_secret*.

    ``'ok'`` marks the row single-use (verified_at) and redirects to login;
    ``'invalid'``/``'expired'`` renders a status template — no 404, no
    enumeration hints. Logged-in users can re-send from their profile.
    """
    result = verify(token_secret)
    if result == 'ok':
        messages.success(request, 'Your email address has been confirmed.')
        return redirect(reverse('accounts:login'))
    return render(
        request,
        'accounts/verify_email.html',
        {'result': result},
    )
