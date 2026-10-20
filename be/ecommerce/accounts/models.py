"""Account models (card d7ac2957 — email verification)."""

import hashlib

from django.conf import settings
from django.db import models
from django.utils import timezone


class EmailVerificationToken(models.Model):
    """One-time token used to verify a user's email address.

    The raw secret lives ONLY in the emailed verification URL; the database
    stores ``sha256(secret)`` hex so a DB dump never becomes a list of live
    verification links (card d7ac2957, design decision 3).

    There is one outstanding (unverified) link per user: issuing a new token
    deletes prior unverified rows for that user. Verified rows are kept as
    history so ``is_email_verified`` can confirm an address was verified at
    some point and still matches the user's current email.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='email_verification_tokens',
    )
    # The address this link verifies — the row's email, not a live read of
    # user.email (an address change invalidates old rows by design).
    email = models.EmailField()
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ('-created_at',)

    def __str__(self):
        status = 'verified' if self.verified_at else ('expired' if self.is_expired() else 'pending')
        return f'EmailVerificationToken({self.user} -> {self.email}: {status})'

    def is_expired(self):
        return timezone.now() > self.expires_at

    @staticmethod
    def hash_token(secret):
        """Return the sha256 hex digest of the raw secret."""
        return hashlib.sha256(secret.encode('utf-8')).hexdigest()
