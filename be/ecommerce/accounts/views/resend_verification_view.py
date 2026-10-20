from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from accounts.verification import is_email_verified, issue_token, send_verification_email
from ecommerce.rate_limit import rate_limit

# login_required outermost: unauthenticated clients are redirected to login
# without consuming rate-limit quota. rate_limit stays outside require_POST so
# a rejected non-POST never consumes quota either.
@login_required
@rate_limit('email_verify')
@require_POST
def resend_verification(request):
    """Re-send the verification email unless the user is already verified."""
    if is_email_verified(request.user):
        messages.info(request, 'Your email address is already verified.')
        return redirect('accounts:profile')
    secret = issue_token(request.user)
    send_verification_email(request, request.user, secret)
    messages.success(request, 'A new verification email has been sent.')
    return redirect('accounts:profile')
