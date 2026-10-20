"""Email verification logic (card d7ac2957).

All verification logic lives here so the views stay thin:

* ``issue_token(user)`` — creates a fresh verification row for the user's
  current email, deleting any prior unverified rows (one outstanding link per
  user). Returns the raw secret the caller embeds in the emailed URL.
* ``verify(secret)`` — hashes the secret, looks up the row and classifies it
  ``'ok'`` / ``'invalid'`` / ``'expired'``. On ``'ok'`` sets ``verified_at``
  (single-use: clicking the same link twice yields ``'invalid'``).
* ``is_email_verified(user)`` — True iff a verified token row exists for this
  user whose ``email`` matches the user's *current* email case-insensitively,
  or (when social auth is enabled) an allauth ``EmailAddress`` row with
  ``verified=True`` matching the current email.

Verification is informational only — it does not gate login, shopping or
checkout (design decision 2). The raw secret never touches the database
(design decision 3): only ``sha256(secret)`` is stored at rest.
"""

import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse
from django.utils import timezone

from accounts.models import EmailVerificationToken

logger = logging.getLogger(__name__)


def issue_token(user):
    """Issue a fresh verification token for *user*'s current email.

    Deletes prior unverified rows for the user (one outstanding link per user;
    verified rows stay as history). Returns the raw secret — never stored.
    """
    EmailVerificationToken.objects.filter(
        user=user, verified_at__isnull=True,
    ).delete()
    secret = secrets.token_urlsafe(32)
    EmailVerificationToken.objects.create(
        user=user,
        email=user.email,
        token_hash=EmailVerificationToken.hash_token(secret),
        expires_at=timezone.now()
        + timedelta(seconds=settings.EMAIL_VERIFICATION_TOKEN_TTL),
    )
    return secret


def verify(secret):
    """Verify *secret* and return a classification string: ``'ok'``,
    ``'invalid'`` or ``'expired'``.

    On ``'ok'`` the row is marked single-use (``verified_at`` set). Never
    trust pre-hashed input — the raw secret is always hashed here.
    """
    token_hash = EmailVerificationToken.hash_token(secret)
    try:
        token = EmailVerificationToken.objects.get(token_hash=token_hash)
    except EmailVerificationToken.DoesNotExist:
        return 'invalid'
    if token.verified_at is not None:
        # Single-use: a link that was already consumed is no longer valid.
        return 'invalid'
    if timezone.now() > token.expires_at:
        return 'expired'
    token.verified_at = timezone.now()
    token.save(update_fields=['verified_at'])
    return 'ok'


def is_email_verified(user):
    """Return True iff *user*'s current email is verified.

    Local regime: a verified token row exists whose ``email`` matches the
    user's current email case-insensitively.

    Social regime (``SOCIAL_AUTH_GOOGLE_ENABLED``): also consults allauth's
    ``EmailAddress`` table for a verified row matching the current email. The
    allauth import lives inside the guarded branch so a missing ``allauth``
    module (disabled regime) or a missing table (mid-migration) can never
    break the page — it degrades to the local-only result.
    """
    if EmailVerificationToken.objects.filter(
        user=user, verified_at__isnull=False,
    ).filter(email__iexact=user.email).exists():
        return True
    if getattr(settings, 'SOCIAL_AUTH_GOOGLE_ENABLED', False):
        try:
            from allauth.account.models import EmailAddress
        except Exception:
            # allauth module missing (disabled regime) or not wired into
            # INSTALLED_APPS (runtime class error): degrade to local-only
            # result rather than breaking the page.
            return False
        try:
            return EmailAddress.objects.filter(
                user=user, email__iexact=user.email, verified=True,
            ).exists()
        except Exception:
            # Table missing mid-migration or any other DB hiccup: degrade to
            # the local-only result rather than breaking the page.
            return False
    return False


def send_verification_email(request, user, secret):
    """Send the verification email (best effort).

    The plain-text body contains the absolute verification URL built from the
    raw secret. A broken mail backend is caught and logged — it must not turn
    registration or a profile save into a 500; users can always resend from
    their profile.
    """
    verify_url = request.build_absolute_uri(
        reverse('accounts:verify_email', args=[secret])
    )
    subject = 'Confirm your email address'
    body = (
        f'Hello {user.username},\n\n'
        f'Please confirm your email address by clicking the link below:\n\n'
        f'{verify_url}\n\n'
        f'The link expires in 72 hours.\n\n'
        f'If you did not create this account, you can ignore this email.\n'
    )
    try:
        send_mail(
            subject,
            body,
            settings.DEFAULT_FROM_EMAIL,
            [user.email],
            fail_silently=False,
        )
    except Exception:
        # A broken mail backend must not turn registration/profile-save into
        # a 500. Users can always resend from the profile.
        logger.warning('Failed to send verification email to %s', user.email)
