"""Tests for the password reset flow (card 9ee2accb).

The flow is Django's built-in one (``PasswordResetView`` and friends) behind
the ``accounts:`` URLs, with the default reset email template. Emails are
captured by Django's locmem backend (overridden per test); the suite runs on
a per-process LocMem cache (see ``ecommerce.test_runner``), so the
``password_reset`` rate-limit counters never touch the shared Redis.
"""

import re

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

User = get_user_model()

PASSWORD_RESET_URL = 'accounts:password_reset'
PASSWORD_RESET_DONE_URL = 'accounts:password_reset_done'
PASSWORD_RESET_CONFIRM_URL = 'accounts:password_reset_confirm'
PASSWORD_RESET_COMPLETE_URL = 'accounts:password_reset_complete'
LOGIN_URL = 'accounts:login'

OLD_PASSWORD = 'complex-pass-phrase-99'
NEW_PASSWORD = 'brand-new-secret-8241'
USERNAME = 'shopper'
EMAIL = 'shopper@example.com'

LOCMEM_EMAIL = 'django.core.mail.backends.locmem.EmailBackend'


def _extract_reset_path(body):
    """Return the relative reset link from a reset email body."""
    match = re.search(r'(/accounts/reset/[^/\s]+/[^/\s]+/)', body)
    assert match is not None, f'No reset link in email body: {body!r}'
    return match.group(1)


class PasswordResetTestCase(TestCase):
    """Base case: the process-wide LocMem cache outlives a single test."""

    def setUp(self):
        super().setUp()
        cache.clear()


class PasswordResetPageTests(PasswordResetTestCase):
    """The reset form renders and the login page points at it."""

    @override_settings(EMAIL_BACKEND=LOCMEM_EMAIL)
    def test_get_renders_email_field(self):
        response = self.client.get(reverse(PASSWORD_RESET_URL))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="email"')
        self.assertContains(response, 'Reset your password')

    def test_login_page_has_forgot_password_link(self):
        response = self.client.get(reverse(LOGIN_URL))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Forgot your password?')
        self.assertContains(response, reverse(PASSWORD_RESET_URL))


class PasswordResetRequestTests(PasswordResetTestCase):
    """POSTing an address sends at most one email, with no enumeration gap."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            USERNAME, EMAIL, OLD_PASSWORD,
        )

    @override_settings(EMAIL_BACKEND=LOCMEM_EMAIL)
    def test_existing_email_sends_one_email_with_resolvable_link(self):
        response = self.client.post(
            reverse(PASSWORD_RESET_URL), {'email': EMAIL},
        )
        self.assertRedirects(response, reverse(PASSWORD_RESET_DONE_URL))
        self.assertEqual(len(mail.outbox), 1)

        message = mail.outbox[0]
        self.assertEqual(message.from_email, settings.DEFAULT_FROM_EMAIL)
        self.assertEqual(message.to, [EMAIL])

        path = _extract_reset_path(message.body)
        match = re.match(r'/accounts/reset/([^/]+)/([^/]+)/$', path)
        self.assertIsNotNone(match)
        uid, token = match.groups()
        # The emailed URL is the one the namespaced route reverses to.
        self.assertEqual(
            path,
            reverse(PASSWORD_RESET_CONFIRM_URL, args=[uid, token]),
        )

    @override_settings(EMAIL_BACKEND=LOCMEM_EMAIL)
    def test_unknown_email_sends_nothing_but_shows_same_page(self):
        response = self.client.post(
            reverse(PASSWORD_RESET_URL), {'email': 'nobody@example.com'},
        )
        # Same redirect as the known-address case: no enumeration signal.
        self.assertRedirects(response, reverse(PASSWORD_RESET_DONE_URL))
        self.assertEqual(len(mail.outbox), 0)

        done = self.client.get(reverse(PASSWORD_RESET_DONE_URL))
        self.assertEqual(done.status_code, 200)
        self.assertContains(
            done, 'If an account exists for that address',
        )


class PasswordResetConfirmTests(PasswordResetTestCase):
    """Token handling: happy path, invalid links and password validators."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            USERNAME, EMAIL, OLD_PASSWORD,
        )

    def _request_link(self):
        """Trigger a reset email and return ``(uid, token, path)``."""
        with override_settings(EMAIL_BACKEND=LOCMEM_EMAIL):
            response = self.client.post(
                reverse(PASSWORD_RESET_URL), {'email': EMAIL},
            )
        self.assertRedirects(response, reverse(PASSWORD_RESET_DONE_URL))
        self.assertEqual(len(mail.outbox), 1)
        path = _extract_reset_path(mail.outbox[0].body)
        match = re.match(r'/accounts/reset/([^/]+)/([^/]+)/$', path)
        self.assertIsNotNone(match)
        uid, token = match.groups()
        return uid, token, path

    def _set_password_path(self, path, token):
        return path.replace(token, 'set-password', 1)

    @override_settings(EMAIL_BACKEND=LOCMEM_EMAIL)
    def test_valid_token_redirects_to_set_password_page(self):
        _, token, path = self._request_link()
        response = self.client.get(path)
        self.assertEqual(response.status_code, 302)
        expected = self._set_password_path(path, token)
        self.assertEqual(response['Location'], expected)

        page = self.client.get(expected)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'name="new_password1"')
        self.assertContains(page, 'name="new_password2"')

    @override_settings(EMAIL_BACKEND=LOCMEM_EMAIL)
    def test_happy_path_sets_new_password(self):
        _, token, path = self._request_link()
        # GET proves the token works (Django redirects to the set-password URL).
        self.assertEqual(self.client.get(path).status_code, 302)

        response = self.client.post(
            self._set_password_path(path, token),
            {
                'new_password1': NEW_PASSWORD,
                'new_password2': NEW_PASSWORD,
            },
        )
        self.assertRedirects(
            response, reverse(PASSWORD_RESET_COMPLETE_URL),
        )

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(NEW_PASSWORD))
        self.assertFalse(self.user.check_password(OLD_PASSWORD))

        self.assertTrue(
            self.client.login(username=USERNAME, password=NEW_PASSWORD),
        )
        self.client.logout()
        self.assertFalse(
            self.client.login(username=USERNAME, password=OLD_PASSWORD),
        )

    @override_settings(EMAIL_BACKEND=LOCMEM_EMAIL)
    def test_bad_uid_and_token_do_not_error(self):
        bad_url = reverse(
            PASSWORD_RESET_CONFIRM_URL,
            args=['invalid-uid', 'invalid-token'],
        )
        response = self.client.get(bad_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'invalid or has expired')

        response = self.client.post(
            bad_url,
            {
                'new_password1': NEW_PASSWORD,
                'new_password2': NEW_PASSWORD,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'invalid or has expired')

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(OLD_PASSWORD))

    @override_settings(EMAIL_BACKEND=LOCMEM_EMAIL)
    def test_weak_password_rejected_by_validators(self):
        _, token, path = self._request_link()
        self.client.get(path)  # Seeds the session's reset token.

        response = self.client.post(
            self._set_password_path(path, token),
            {'new_password1': '123', 'new_password2': '123'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'too short')

        self.user.refresh_from_db()
        self.assertFalse(self.user.check_password('123'))
        self.assertTrue(self.user.check_password(OLD_PASSWORD))


class PasswordResetRateLimitTests(PasswordResetTestCase):
    """Only the POST that sends the email is rate-limited."""

    @override_settings(RATE_LIMIT_PASSWORD_RESET=(2, 3600))
    def test_third_post_is_rate_limited(self):
        url = reverse(PASSWORD_RESET_URL)
        for _ in range(2):
            response = self.client.post(
                url, {'email': 'nobody@example.com'},
            )
            self.assertRedirects(
                response, reverse(PASSWORD_RESET_DONE_URL),
            )

        response = self.client.post(url, {'email': 'nobody@example.com'})
        self.assertEqual(response.status_code, 429)
        self.assertContains(
            response, 'Too many requests', status_code=429,
        )
        self.assertIn('Retry-After', response)

    @override_settings(RATE_LIMIT_PASSWORD_RESET=(1, 3600))
    def test_get_requests_are_never_limited(self):
        url = reverse(PASSWORD_RESET_URL)
        for _ in range(3):
            self.assertEqual(self.client.get(url).status_code, 200)

        self.client.post(url, {'email': 'nobody@example.com'})
        response = self.client.post(url, {'email': 'nobody@example.com'})
        self.assertEqual(response.status_code, 429)
