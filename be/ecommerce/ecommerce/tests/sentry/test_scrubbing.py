"""Tests for Sentry sensitive-data scrubbing (card 5ggb4929).

Covers:
* ``_is_sensitive_key`` suffix/whole-key matching for payment/credential keys.
* ``SentryScrubbingProcessor`` redacts sensitive field values to
  ``***REDACTED***`` while preserving safe Stripe correlation tokens
  (``idempotency_key``, ``client_reference_id``, ``order_id``, ``request_id``).
* The processor walks nested dicts, lists, request data, cookies, extra,
  user, and contexts.
* Cookie names matching session/auth patterns are redacted.
* ``before_send`` returns the mutated event (never drops it to None silently
  for scrubbing reasons).
"""
from django.test import SimpleTestCase

from ecommerce.sentry import SentryScrubbingProcessor, _is_sensitive_key


class IsSensitiveKeyTests(SimpleTestCase):
    """``_is_sensitive_key`` matches suffix + whole-key names."""

    def test_whole_key_match(self):
        self.assertTrue(_is_sensitive_key('password'))
        self.assertTrue(_is_sensitive_key('secret'))
        self.assertTrue(_is_sensitive_key('cvv'))
        self.assertTrue(_is_sensitive_key('card_number'))

    def test_dotted_path_suffix_match(self):
        self.assertTrue(_is_sensitive_key('user.password'))
        self.assertTrue(_is_sensitive_key('request.card.cvc'))
        self.assertTrue(_is_sensitive_key('stripe.stripe_secret_key'))

    def test_case_insensitive(self):
        self.assertTrue(_is_sensitive_key('UserPassword'))
        self.assertTrue(_is_sensitive_key('CARD_NUMBER'))
        self.assertTrue(_is_sensitive_key('Stripe_Secret_Key'))

    def test_safe_keys_not_sensitive(self):
        self.assertFalse(_is_sensitive_key('name'))
        self.assertFalse(_is_sensitive_key('email'))
        self.assertFalse(_is_sensitive_key('idempotency_key'))
        self.assertFalse(_is_sensitive_key('client_reference_id'))
        self.assertFalse(_is_sensitive_key('order_id'))
        self.assertFalse(_is_sensitive_key('request_id'))

    def test_unrelated_suffix_not_sensitive(self):
        # 'passwords' should not match 'password' (suffix, not substring).
        self.assertFalse(_is_sensitive_key('passwords'))
        self.assertFalse(_is_sensitive_key('secretary'))


class ScrubbingProcessorTests(SimpleTestCase):
    """The before_send processor redacts sensitive values from events."""

    def setUp(self):
        self.proc = SentryScrubbingProcessor()

    def test_returns_event_dict(self):
        event = {'message': 'boom'}
        self.assertIs(self.proc(event, {}), event)

    def test_redacts_top_level_sensitive(self):
        event = {
            'message': 'payment failed',
            'password': 's3cret',
            'api_key': 'k123',
            'name': 'ok',
        }
        self.proc(event, {})
        self.assertEqual(event['password'], '***REDACTED***')
        self.assertEqual(event['api_key'], '***REDACTED***')
        self.assertEqual(event['name'], 'ok')

    def test_preserves_stripe_correlation_tokens(self):
        event = {
            'idempotency_key': 'order-123',
            'client_reference_id': 'cus_abc',
            'order_id': 'order-456',
            'request_id': 'req_789',
            'stripe_secret_key': 'sk_test_123',
        }
        self.proc(event, {})
        self.assertEqual(event['idempotency_key'], 'order-123')
        self.assertEqual(event['client_reference_id'], 'cus_abc')
        self.assertEqual(event['order_id'], 'order-456')
        self.assertEqual(event['request_id'], 'req_789')
        self.assertEqual(event['stripe_secret_key'], '***REDACTED***')

    def test_redacts_nested_extra(self):
        event = {
            'extra': {
                'user': {'password': 'p', 'email': 'a@b.com'},
                'card_cvc': '123',
                'safe': 'ok',
            },
        }
        self.proc(event, {})
        self.assertEqual(event['extra']['user']['password'], '***REDACTED***')
        self.assertEqual(event['extra']['user']['email'], 'a@b.com')
        self.assertEqual(event['extra']['card_cvc'], '***REDACTED***')
        self.assertEqual(event['extra']['safe'], 'ok')

    def test_redacts_request_data(self):
        event = {
            'request': {
                'url': 'https://shop.test/checkout',
                'data': {
                    'card_number': '4111111111111111',
                    'cvc': '999',
                    'email': 'a@b.com',
                    'first_name': 'ok',
                },
            },
        }
        self.proc(event, {})
        data = event['request']['data']
        self.assertEqual(data['card_number'], '***REDACTED***')
        self.assertEqual(data['cvc'], '***REDACTED***')
        self.assertEqual(data['email'], 'a@b.com')
        self.assertEqual(data['first_name'], 'ok')

    def test_redacts_request_cookies(self):
        event = {
            'request': {
                'cookies': {
                    'sessionid': 'long-session-value',
                    'csrftoken': 'csrf-value',
                    'preferences': 'theme=dark',
                },
            },
        }
        self.proc(event, {})
        cookies = event['request']['cookies']
        self.assertEqual(cookies['sessionid'], '***REDACTED***')
        self.assertEqual(cookies['csrftoken'], '***REDACTED***')
        # Non-auth cookies are preserved.
        self.assertEqual(cookies['preferences'], 'theme=dark')

    def test_redacts_nested_lists(self):
        event = {
            'extra': [
                {'password': 'p1'},
                {'name': 'ok'},
            ],
        }
        self.proc(event, {})
        self.assertEqual(event['extra'][0]['password'], '***REDACTED***')
        self.assertEqual(event['extra'][1]['name'], 'ok')

    def test_redacts_user_and_contexts(self):
        event = {
            'user': {'id': 1, 'email': 'a@b.com'},
            'contexts': {'stripe': {'stripe_secret_key': 'sk'}},
            'extra': {'safe': 1},
        }
        self.proc(event, {})
        # user context has no sensitive keys -> untouched
        self.assertEqual(event['user']['email'], 'a@b.com')
        self.assertEqual(event['contexts']['stripe']['stripe_secret_key'], '***REDACTED***')
        self.assertEqual(event['extra']['safe'], 1)

    def test_empty_event_returns_itself(self):
        empty = {}
        self.assertIs(self.proc(empty, {}), empty)
        self.assertIs(self.proc(None, {}), None)

    def test_deeply_nested_redaction(self):
        event = {
            'extra': {
                'a': {'b': {'c': {'password': 'deep'}}},
            },
        }
        self.proc(event, {})
        self.assertEqual(event['extra']['a']['b']['c']['password'], '***REDACTED***')

    def test_does_not_drop_exception_events(self):
        """before_send must never return None (which would drop the event)."""
        event = {'message': 'error', 'password': 'x'}
        result = self.proc(event, {})
        self.assertIsNotNone(result)


class ScrubbingProcessorConfigTests(SimpleTestCase):
    """The processor honours SentryConfig scrub flags when provided."""

    def _make_cfg(self, **overrides):
        from ecommerce.sentry_config import SentryConfig
        defaults = dict(
            dsn='https://x@o0.ingest.sentry.io/0',
            environment='test',
            release='test',
            traces_sample_rate=0.2,
            profiles_sample_rate=0.0,
            send_default_pii=False,
            scrub_request_headers=True,
            scrub_request_cookies=True,
            scrub_payment_data=True,
            sensitive_fields=('password',),
            celery_enabled=True,
            django_enabled=True,
            options={},
        )
        defaults.update(overrides)
        return SentryConfig(**defaults)

    def test_scrub_payment_disabled_leaves_sensitive_keys(self):
        """When scrub_payment_data=False, sensitive field values are preserved."""
        cfg = self._make_cfg(scrub_payment_data=False)
        proc = SentryScrubbingProcessor(cfg)
        event = {'password': 'secret', 'card_number': '4111', 'name': 'ok'}
        proc(event, {})
        self.assertEqual(event['password'], 'secret')
        self.assertEqual(event['card_number'], '4111')
        self.assertEqual(event['name'], 'ok')

    def test_scrub_payment_enabled_redacts_sensitive_keys(self):
        """When scrub_payment_data=True, sensitive field values are redacted."""
        cfg = self._make_cfg(scrub_payment_data=True)
        proc = SentryScrubbingProcessor(cfg)
        event = {'password': 'secret', 'card_number': '4111', 'name': 'ok'}
        proc(event, {})
        self.assertEqual(event['password'], '***REDACTED***')
        self.assertEqual(event['card_number'], '***REDACTED***')
        self.assertEqual(event['name'], 'ok')

    def test_safe_stripe_tokens_always_preserved_even_with_scrub_off(self):
        """Stripe correlation tokens are kept regardless of scrub payment flag."""
        cfg = self._make_cfg(scrub_payment_data=False)
        proc = SentryScrubbingProcessor(cfg)
        event = {
            'idempotency_key': 'order-123',
            'client_reference_id': 'cus_abc',
            'stripe_secret_key': 'sk_test_123',
        }
        proc(event, {})
        self.assertEqual(event['idempotency_key'], 'order-123')
        self.assertEqual(event['client_reference_id'], 'cus_abc')

    def test_default_processor_scrubs_without_config(self):
        """Without a SentryConfig, the processor is fail-safe (scrubs by default)."""
        proc = SentryScrubbingProcessor()
        event = {'password': 'secret', 'name': 'ok'}
        proc(event, {})
        self.assertEqual(event['password'], '***REDACTED***')
        self.assertEqual(event['name'], 'ok')

    def test_cookie_scrub_disabled_preserves_session_cookies(self):
        """When scrub_request_cookies=False, session/cookie values are preserved."""
        cfg = self._make_cfg(scrub_request_cookies=False)
        proc = SentryScrubbingProcessor(cfg)
        event = {
            'request': {
                'cookies': {
                    'sessionid': 'long-session-value',
                    'preferences': 'theme=dark',
                },
            },
        }
        proc(event, {})
        cookies = event['request']['cookies']
        self.assertEqual(cookies['sessionid'], 'long-session-value')
        self.assertEqual(cookies['preferences'], 'theme=dark')
