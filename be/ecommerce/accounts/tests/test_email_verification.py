"""Tests for app-level email verification (card d7ac2957).

The suite runs on a per-process LocMem cache (see ecommerce.test_runner),
so rate-limit counters never touch the shared Redis. Emails are captured by
Django's locmem email backend (overridden below) and inspected via
``django.core.mail.outbox``.
"""

import re
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import EmailVerificationToken
from accounts.verification import is_email_verified, issue_token, verify

User = get_user_model()

REGISTER_URL_NAME = 'accounts:register'
PROFILE_URL_NAME = 'accounts:profile'
RESEND_URL_NAME = 'accounts:resend_verification'


def _extract_secret(body):
    """Pull the raw verification token out of the emailed absolute URL."""
    match = re.search(r'verify-email/([^/]+)/', body)
    assert match is not None, f'No verify-email URL in email body: {body!r}'
    return match.group(1)


def _email_count():
    return len(mail.outbox)


class RegistrationVerificationTests(TestCase):
    """Registration happy path issues exactly one verification email."""

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    )
    def test_registration_sends_one_verification_email(self):
        before = _email_count()
        response = self.client.post(
            reverse(REGISTER_URL_NAME),
            {
                'username': 'newshopper',
                'email': 'new@example.com',
                'password1': 'complex-pass-phrase-99',
                'password2': 'complex-pass-phrase-99',
            },
        )
        self.assertRedirects(response, reverse(PROFILE_URL_NAME))
        self.assertEqual(_email_count(), before + 1)
        self.assertEqual(mail.outbox[-1].subject, 'Confirm your email address')

        secret = _extract_secret(mail.outbox[-1].body)
        user = User.objects.get(username='newshopper')
        token = EmailVerificationToken.objects.get(user=user)

        # The row's email is the registration address; not yet verified.
        self.assertEqual(token.email, 'new@example.com')
        self.assertIsNone(token.verified_at)
        # Hash-at-rest: the raw secret never equals the stored digest.
        self.assertNotEqual(secret, token.token_hash)
        self.assertEqual(
            EmailVerificationToken.hash_token(secret), token.token_hash,
        )

        # The URL is resolvable: visiting it verifies.
        self.assertEqual(verify(secret), 'ok')
        token.refresh_from_db()
        self.assertIsNotNone(token.verified_at)


class VerifyViewTests(TestCase):
    """The verify-email view classifies and consumes tokens."""

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    )
    def _register(self, username='usera', email='a@example.com'):
        # Log out first so a previously registered user doesn't block this
        # registration (the register view redirects authenticated users).
        self.client.logout()
        before = _email_count()
        response = self.client.post(
            reverse(REGISTER_URL_NAME),
            {
                'username': username,
                'email': email,
                'password1': 'complex-pass-phrase-99',
                'password2': 'complex-pass-phrase-99',
            },
        )
        self.assertRedirects(response, reverse(PROFILE_URL_NAME))
        self.assertEqual(_email_count(), before + 1)
        secret = _extract_secret(mail.outbox[-1].body)
        return User.objects.get(username=username), secret

    def test_verify_success_sets_verified_at(self):
        user, secret = self._register()
        token = EmailVerificationToken.objects.get(user=user)
        self.assertIsNone(token.verified_at)

        self.assertEqual(verify(secret), 'ok')
        token.refresh_from_db()
        self.assertIsNotNone(token.verified_at)

    def test_verify_single_use(self):
        _user, secret = self._register()
        self.assertEqual(verify(secret), 'ok')
        # Clicking the same link again is invalid (single-use).
        self.assertEqual(verify(secret), 'invalid')

    def test_verify_unknown_secret(self):
        self.assertEqual(verify('does-not-exist'), 'invalid')

    def test_verify_expired(self):
        _user, secret = self._register()
        token = EmailVerificationToken.objects.get(
            user__email='a@example.com',
        )
        token.expires_at = timezone.now() - timezone.timedelta(hours=1)
        token.save(update_fields=['expires_at'])
        self.assertEqual(verify(secret), 'expired')

    def test_verify_row_of_user_a_does_not_verify_user_b(self):
        user_a, secret_a = self._register(username='usera', email='a@example.com')
        user_b, _secret_b = self._register(username='userb', email='b@example.com')
        # user_b must not be able to consume user_a's token.
        self.assertEqual(verify(secret_a), 'ok')
        token_a = EmailVerificationToken.objects.get(user=user_a)
        token_b = EmailVerificationToken.objects.get(user=user_b)
        self.assertIsNotNone(token_a.verified_at)
        self.assertIsNone(token_b.verified_at)


class ProfileBadgeTests(TestCase):
    """The profile badge reflects verification of the CURRENT email."""

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    )
    def _register_and_login(self, email='profile@example.com'):
        self.client.post(
            reverse(REGISTER_URL_NAME),
            {
                'username': 'profileuser',
                'email': email,
                'password1': 'complex-pass-phrase-99',
                'password2': 'complex-pass-phrase-99',
            },
        )
        return User.objects.get(email=email)

    def test_badge_unverified_before_verified_after(self):
        user = self._register_and_login()
        self.assertFalse(is_email_verified(user))

        secret = _extract_secret(mail.outbox[-1].body)
        self.assertEqual(verify(secret), 'ok')
        self.assertTrue(is_email_verified(user))

    def test_badge_uses_current_email_change_email_unverified_again(self):
        user = self._register_and_login(email='old@example.com')
        secret = _extract_secret(mail.outbox[-1].body)
        self.assertEqual(verify(secret), 'ok')
        self.assertTrue(is_email_verified(user))

        # Change the email: the old verified row no longer matches the current
        # email, so the badge flips back to unverified.
        response = self.client.post(
            reverse(PROFILE_URL_NAME),
            {
                'first_name': user.first_name,
                'last_name': user.last_name,
                'email': 'new@example.com',
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.email, 'new@example.com')
        self.assertFalse(is_email_verified(user))


class ProfileEmailChangeTests(TestCase):
    """Changing the email issues a fresh token for the new address."""

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    )
    def _register_and_login(self, email):
        self.client.post(
            reverse(REGISTER_URL_NAME),
            {
                'username': 'profileuser',
                'email': email,
                'password1': 'complex-pass-phrase-99',
                'password2': 'complex-pass-phrase-99',
            },
        )
        return User.objects.get(email=email)

    def test_email_change_issues_new_token_and_email(self):
        user = self._register_and_login(email='old@example.com')
        old_secret = _extract_secret(mail.outbox[-1].body)
        # The old address is verified first.
        self.assertEqual(verify(old_secret), 'ok')
        self.assertTrue(is_email_verified(user))

        before = _email_count()
        self.client.post(
            reverse(PROFILE_URL_NAME),
            {
                'first_name': user.first_name,
                'last_name': user.last_name,
                'email': 'new@example.com',
            },
        )
        # Exactly one more email went out for the new address.
        self.assertEqual(_email_count(), before + 1)
        user.refresh_from_db()
        self.assertEqual(user.email, 'new@example.com')

        # A fresh unverified token row exists for the new address.
        new_token = EmailVerificationToken.objects.get(
            user=user, email='new@example.com',
        )
        self.assertIsNone(new_token.verified_at)

        # The verified row belongs to the OLD address, so the NEW address is
        # not considered verified (the old secret is single-use / consumed).
        self.assertFalse(is_email_verified(user))


class ResendVerificationTests(TestCase):
    """The resend view issues tokens and is rate-limited."""

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        RATE_LIMIT_EMAIL_VERIFY=(2, 3600),
    )
    def test_resend_unverified_issues_token_and_email(self):
        cache.clear()
        user = User.objects.create_user(
            'resenduser', 'resend@example.com', 'complex-pass-phrase-99',
        )
        self.client.force_login(user)
        before = _email_count()
        before_tokens = EmailVerificationToken.objects.filter(user=user).count()

        response = self.client.post(reverse(RESEND_URL_NAME))
        self.assertRedirects(response, reverse(PROFILE_URL_NAME))
        self.assertEqual(_email_count(), before + 1)
        self.assertEqual(
            EmailVerificationToken.objects.filter(user=user).count(),
            before_tokens + 1,
        )

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        RATE_LIMIT_EMAIL_VERIFY=(2, 3600),
    )
    def test_resend_verified_user_does_not_respin(self):
        cache.clear()
        user = User.objects.create_user(
            'verifieduser', 'verified@example.com', 'complex-pass-phrase-99',
        )
        EmailVerificationToken.objects.create(
            user=user,
            email=user.email,
            token_hash=EmailVerificationToken.hash_token('some-secret'),
            expires_at=timezone.now() + timezone.timedelta(hours=1),
            verified_at=timezone.now(),
        )
        self.client.force_login(user)
        before = _email_count()
        before_tokens = EmailVerificationToken.objects.filter(user=user).count()

        response = self.client.post(reverse(RESEND_URL_NAME))
        self.assertRedirects(response, reverse(PROFILE_URL_NAME))
        self.assertEqual(_email_count(), before)
        self.assertEqual(
            EmailVerificationToken.objects.filter(user=user).count(),
            before_tokens,
        )

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        RATE_LIMIT_EMAIL_VERIFY=(2, 3600),
    )
    def test_resend_rate_limit_429(self):
        cache.clear()
        user = User.objects.create_user(
            'ratelimituser', 'ratelimit@example.com', 'complex-pass-phrase-99',
        )
        self.client.force_login(user)
        responses = [self.client.post(reverse(RESEND_URL_NAME)) for _ in range(3)]
        self.assertEqual(responses[0].status_code, 302)
        self.assertEqual(responses[1].status_code, 302)
        self.assertEqual(responses[2].status_code, 429)
        self.assertIn('Retry-After', responses[2].headers)


class IsEmailVerifiedSocialFlagTests(TestCase):
    """is_email_verified degrades gracefully when social auth flag is on but
    allauth is absent (no import / table errors)."""

    @override_settings(SOCIAL_AUTH_GOOGLE_ENABLED=True)
    def test_returns_local_result_without_allauth(self):
        user = User.objects.create_user(
            'socialuser', 'social@example.com', 'complex-pass-phrase-99',
        )
        # No verified token: local result is False, no allauth import error.
        self.assertFalse(is_email_verified(user))

        # A verified token still yields the local True result.
        EmailVerificationToken.objects.create(
            user=user,
            email=user.email,
            token_hash=EmailVerificationToken.hash_token('some-secret'),
            expires_at=timezone.now() + timezone.timedelta(hours=1),
            verified_at=timezone.now(),
        )
        self.assertTrue(is_email_verified(user))


class MailFailureTests(TestCase):
    """A broken mail backend must not turn registration into a 500."""

    @patch('accounts.verification.send_mail', side_effect=Exception('SMTP down'))
    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    )
    def test_registration_still_succeeds_when_mail_fails(self, _mock_send_mail):
        response = self.client.post(
            reverse(REGISTER_URL_NAME),
            {
                'username': 'mailfail',
                'email': 'mailfail@example.com',
                'password1': 'complex-pass-phrase-99',
                'password2': 'complex-pass-phrase-99',
            },
        )
        # Registration still succeeds (redirect) despite the mail failure.
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(username='mailfail')
        # The token row was still issued (best effort).
        self.assertTrue(
            EmailVerificationToken.objects.filter(user=user).exists()
        )
