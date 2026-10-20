import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse

from accounts.forms import ProfileForm
from accounts.verification import is_email_verified, issue_token, send_verification_email

logger = logging.getLogger(__name__)


@login_required
def profile(request):
    email_verified = is_email_verified(request.user)
    if request.method == 'POST':
        form = ProfileForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            if 'email' in form.changed_data:
                # The address changed: the old verified row no longer matches
                # the current email, so issue a fresh token for re-verification
                # of the new address (best effort).
                try:
                    secret = issue_token(request.user)
                    send_verification_email(request, request.user, secret)
                except Exception:
                    logger.warning(
                        'Failed to issue/send verification email for user %s',
                        request.user.pk,
                    )
            messages.success(request, 'Your profile was updated.')
            return redirect(reverse('accounts:profile'))
    else:
        form = ProfileForm(instance=request.user)
    return render(
        request,
        'accounts/profile.html',
        {
            'form': form,
            'email_verified': email_verified,
        },
    )
