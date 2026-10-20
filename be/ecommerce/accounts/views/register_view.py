import logging

from django.shortcuts import redirect, render
from django.urls import reverse

from accounts.forms import RegisterForm
from accounts.verification import issue_token, send_verification_email
from ecommerce.csrf_rotation import login_with_csrf_rotation

logger = logging.getLogger(__name__)


def register(request):
    if request.user.is_authenticated:
        return redirect(reverse('accounts:profile'))
    if request.method == 'POST':
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            # Best effort: issue a token and send the verification email. A
            # broken mail backend must not turn registration into a 500 —
            # send_verification_email catches and logs mail errors internally.
            try:
                secret = issue_token(user)
                send_verification_email(request, user, secret)
            except Exception:
                logger.warning(
                    'Failed to issue/send verification email for user %s', user.pk
                )
            login_with_csrf_rotation(request, user)
            return redirect(reverse('accounts:profile'))
    else:
        form = RegisterForm()
    return render(request, 'accounts/register.html', {'form': form})
