"""Tests for the Sentry configuration helper (card 5ggb4929).

Covers:
* ``SENTRY_DSN`` unset/empty -> ``None`` config (Sentry off).
* ``SENTRY_DSN`` set -> a ``SentryConfig`` with the right fields.
* ``SENTRY_ENVIRONMENT`` / ``SENTRY_RELEASE`` overrides and auto-detection.
* ``SENTRY_TRACES_SAMPLE_RATE`` / ``SENTRY_PROFILES_SAMPLE_RATE`` parsing.
* ``SENTRY_SEND_DEFAULT_PII`` boolean parsing.
* Scrubbing toggles default True (request headers, cookies, payment data).
* ``SENTRY_SENSITIVE_FIELDS`` extends (not replaces) the payment set.
* ``SENTRY_CELERY_ENABLED`` / ``SENTRY_DJANGO_ENABLED`` toggles.
* ``is_sentry_enabled()`` reads the live environment.
* No network and no SDK import required — pure env-parsing tests.
"""
import os
from unittest.mock import patch

from django.test import SimpleTestCase

from ecommerce.sentry_config import (
    SENTRY_DSN_ENV,
    SentryConfig,
    is_sentry_enabled,
    sentry_config,
)


def _clear_sentry_env():
    """Remove every SENTRY_* var (restore to a clean baseline)."""
    env = os.environ
    for key in list(env):
        if key.startswith('SENTRY_') or key in ('GIT_COMMIT', 'GIT_SHA', 'APP_VERSION', 'VERSION'):
            env.pop(key, None)


class SentryConfigDisabledTests(SimpleTestCase):
    """When SENTRY_DSN is unset/empty, sentry_config returns None."""

    def setUp(self):
        _clear_sentry_env()

    def test_unset_dsn_returns_none(self):
        config = sentry_config(os.environ)
        self.assertIsNone(config)

    def test_empty_dsn_returns_none(self):
        with patch.dict(os.environ, {'SENTRY_DSN': ''}):
            config = sentry_config(os.environ)
        self.assertIsNone(config)

    def test_whitespace_dsn_returns_none(self):
        with patch.dict(os.environ, {'SENTRY_DSN': '   '}):
            config = sentry_config(os.environ)
        self.assertIsNone(config)

    def test_is_sentry_enabled_false_when_unset(self):
        _clear_sentry_env()
        self.assertFalse(is_sentry_enabled())

    def test_is_sentry_enabled_false_when_empty(self):
        with patch.dict(os.environ, {'SENTRY_DSN': ''}, clear=False):
            # Ensure SENTRY_DSN is explicitly empty.
            os.environ['SENTRY_DSN'] = ''
            self.assertFalse(is_sentry_enabled())


class SentryConfigEnabledTests(SimpleTestCase):
    """When SENTRY_DSN is set, a fully-populated config is built."""

    def setUp(self):
        _clear_sentry_env()

    def test_dsn_is_required_and_preserved(self):
        with patch.dict(os.environ, {'SENTRY_DSN': 'https://x@sentry.io/123'}):
            config = sentry_config(os.environ)
        self.assertIsNotNone(config)
        self.assertEqual(config.dsn, 'https://x@sentry.io/123')

    def test_environment_defaults_to_unknown(self):
        with patch.dict(os.environ, {'SENTRY_DSN': 'https://x@sentry.io/123'}):
            config = sentry_config(os.environ)
        self.assertEqual(config.environment, 'unknown')

    def test_environment_override(self):
        with patch.dict(os.environ, {
            'SENTRY_DSN': 'https://x@sentry.io/123',
            'SENTRY_ENVIRONMENT': 'production',
        }):
            config = sentry_config(os.environ)
        self.assertEqual(config.environment, 'production')

    def test_release_defaults_to_unreleased(self):
        _clear_sentry_env()
        with patch.dict(os.environ, {'SENTRY_DSN': 'https://x@sentry.io/123'}, clear=False):
            os.environ.pop('GIT_COMMIT', None)
            os.environ.pop('APP_VERSION', None)
            config = sentry_config(os.environ)
        self.assertEqual(config.release, 'django-ecommerce@unreleased')

    def test_release_explicit_override(self):
        with patch.dict(os.environ, {
            'SENTRY_DSN': 'https://x@sentry.io/123',
            'SENTRY_RELEASE': 'v1.2.3',
        }):
            config = sentry_config(os.environ)
        self.assertEqual(config.release, 'v1.2.3')

    def test_release_auto_from_git_commit(self):
        with patch.dict(os.environ, {
            'SENTRY_DSN': 'https://x@sentry.io/123',
            'GIT_COMMIT': 'abc123',
        }):
            config = sentry_config(os.environ)
        self.assertEqual(config.release, 'abc123')

    def test_release_auto_from_app_version(self):
        with patch.dict(os.environ, {
            'SENTRY_DSN': 'https://x@sentry.io/123',
            'APP_VERSION': '2.0.0',
        }):
            config = sentry_config(os.environ)
        self.assertEqual(config.release, '2.0.0')

    def test_release_explicit_wins_over_git(self):
        with patch.dict(os.environ, {
            'SENTRY_DSN': 'https://x@sentry.io/123',
            'SENTRY_RELEASE': 'explicit',
            'GIT_COMMIT': 'ignored',
        }):
            config = sentry_config(os.environ)
        self.assertEqual(config.release, 'explicit')

    def test_traces_sample_rate_default(self):
        with patch.dict(os.environ, {'SENTRY_DSN': 'https://x@sentry.io/123'}):
            config = sentry_config(os.environ)
        self.assertEqual(config.traces_sample_rate, 0.2)

    def test_traces_sample_rate_override(self):
        with patch.dict(os.environ, {
            'SENTRY_DSN': 'https://x@sentry.io/123',
            'SENTRY_TRACES_SAMPLE_RATE': '0.5',
        }):
            config = sentry_config(os.environ)
        self.assertEqual(config.traces_sample_rate, 0.5)

    def test_traces_sample_rate_invalid_falls_back_to_default(self):
        with patch.dict(os.environ, {
            'SENTRY_DSN': 'https://x@sentry.io/123',
            'SENTRY_TRACES_SAMPLE_RATE': 'not-a-number',
        }):
            config = sentry_config(os.environ)
        self.assertEqual(config.traces_sample_rate, 0.2)

    def test_profiles_sample_rate_default_zero(self):
        with patch.dict(os.environ, {'SENTRY_DSN': 'https://x@sentry.io/123'}):
            config = sentry_config(os.environ)
        self.assertEqual(config.profiles_sample_rate, 0.0)

    def test_send_default_pii_defaults_false(self):
        with patch.dict(os.environ, {'SENTRY_DSN': 'https://x@sentry.io/123'}):
            config = sentry_config(os.environ)
        self.assertFalse(config.send_default_pii)

    def test_send_default_pii_true_when_set(self):
        with patch.dict(os.environ, {
            'SENTRY_DSN': 'https://x@sentry.io/123',
            'SENTRY_SEND_DEFAULT_PII': '1',
        }):
            config = sentry_config(os.environ)
        self.assertTrue(config.send_default_pii)

    def test_scrub_flags_default_true(self):
        with patch.dict(os.environ, {'SENTRY_DSN': 'https://x@sentry.io/123'}):
            config = sentry_config(os.environ)
        self.assertTrue(config.scrub_request_headers)
        self.assertTrue(config.scrub_request_cookies)
        self.assertTrue(config.scrub_payment_data)

    def test_scrub_flags_respect_false(self):
        with patch.dict(os.environ, {
            'SENTRY_DSN': 'https://x@sentry.io/123',
            'SENTRY_SCRUB_REQUEST_HEADERS': 'false',
            'SENTRY_SCRUB_REQUEST_COOKIES': '0',
            'SENTRY_SCRUB_PAYMENT_DATA': 'no',
        }):
            config = sentry_config(os.environ)
        self.assertFalse(config.scrub_request_headers)
        self.assertFalse(config.scrub_request_cookies)
        self.assertFalse(config.scrub_payment_data)

    def test_integration_flags_default_true(self):
        with patch.dict(os.environ, {'SENTRY_DSN': 'https://x@sentry.io/123'}):
            config = sentry_config(os.environ)
        self.assertTrue(config.celery_enabled)
        self.assertTrue(config.django_enabled)

    def test_integration_flags_respect_false(self):
        with patch.dict(os.environ, {
            'SENTRY_DSN': 'https://x@sentry.io/123',
            'SENTRY_CELERY_ENABLED': 'false',
            'SENTRY_DJANGO_ENABLED': '0',
        }):
            config = sentry_config(os.environ)
        self.assertFalse(config.celery_enabled)
        self.assertFalse(config.django_enabled)

    def test_sensitive_fields_defaults_include_payment_keys(self):
        with patch.dict(os.environ, {'SENTRY_DSN': 'https://x@sentry.io/123'}):
            config = sentry_config(os.environ)
        fields = set(config.sensitive_fields)
        # Built-in payment/credential keys are always present.
        self.assertIn('stripe_secret_key', fields)
        self.assertIn('card_number', fields)
        self.assertIn('cvv', fields)
        self.assertIn('password', fields)

    def test_sensitive_fields_extra_extends_not_replaces(self):
        with patch.dict(os.environ, {
            'SENTRY_DSN': 'https://x@sentry.io/123',
            'SENTRY_SENSITIVE_FIELDS': 'custom_secret,api_token',
        }):
            config = sentry_config(os.environ)
        fields = set(config.sensitive_fields)
        # Built-ins still present.
        self.assertIn('stripe_secret_key', fields)
        # Extras added.
        self.assertIn('custom_secret', fields)
        self.assertIn('api_token', fields)

    def test_sensitive_fields_extra_normalized_to_lowercase(self):
        with patch.dict(os.environ, {
            'SENTRY_DSN': 'https://x@sentry.io/123',
            'SENTRY_SENSITIVE_FIELDS': 'CustomField,MixedCase',
        }):
            config = sentry_config(os.environ)
        fields = set(config.sensitive_fields)
        self.assertIn('customfield', fields)
        self.assertIn('mixedcase', fields)


class SentryConfigImmutabilityTests(SimpleTestCase):
    """SentryConfig is frozen — scrubbing options can't be mutated after build."""

    def setUp(self):
        _clear_sentry_env()

    def test_config_is_frozen(self):
        with patch.dict(os.environ, {'SENTRY_DSN': 'https://x@sentry.io/123'}):
            config = sentry_config(os.environ)
        self.assertIsNotNone(config)
        with self.assertRaises(Exception):
            config.dsn = 'https://changed@sentry.io/456'  # type: ignore[misc]


class SentrySettingsIntegrationTests(SimpleTestCase):
    """The settings module exposes SENTRY_CONFIG built from the environment."""

    def test_settings_has_sentry_config_attr(self):
        from django.conf import settings
        self.assertTrue(hasattr(settings, 'SENTRY_CONFIG'))

    def test_settings_sentry_config_none_when_dsn_unset(self):
        # Default test environment: no SENTRY_DSN.
        from django.conf import settings
        self.assertIsNone(settings.SENTRY_CONFIG)
