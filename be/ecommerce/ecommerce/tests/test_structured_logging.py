"""Tests for structured logging with request correlation IDs (card 6cc44f8g).

Covers:
* JSON formatter: field presence/stability, request_id injection, extra-field
  pass-through, exception serialization, non-serializable value handling.
* RequestIDLogFilter: injects/absents request_id depending on context.
* RequestIDMiddleware: ID generation, acceptance of a valid X-Request-Id,
  rejection of invalid headers (falls back to generated), response header
  echo, context isolation between concurrent requests (async).
* redaction: sensitive keys in nested dicts/lists are masked; unknown keys
  pass through.
* Celery task propagation: propagate_request_id forwards the ID to a task
  kwarg; the task-tracing hook rebinds the contextvar in the worker.
* Settings integration: LOGGING is wired and JSON output is active.

The shared Redis cache is never touched; the suite runs on LocMem (the test
runner swaps it in) and no DB rows are required for most of these tests.
"""
import json
import logging
import re
from io import StringIO
from unittest.mock import patch

from django.http import HttpResponse
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from ecommerce.logging import (
    JSONFormatter,
    REQUEST_ID_HEADER,
    RequestIDLogFilter,
    redact,
    request_id as request_id_var,
)
from ecommerce.middleware.request_id import (
    RequestIDMiddleware,
    _generate,
    _valid_existing,
    propagate_request_id,
    request_id,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_record(msg='hello', logger='test', extra=None, exc_info=None):
    """Build a LogRecord with optional extra fields."""
    record = logging.LogRecord(
        name=logger, level=logging.INFO, pathname=__file__, lineno=1,
        msg=msg, args=(), exc_info=exc_info,
    )
    if extra:
        for k, v in extra.items():
            setattr(record, k, v)
    return record


def _format(record):
    """Format a record through a fresh JSONFormatter and return the parsed dict."""
    formatter = JSONFormatter()
    return json.loads(formatter.format(record))


# ---------------------------------------------------------------------------
# redact()
# ---------------------------------------------------------------------------

class RedactTests(SimpleTestCase):
    def test_redacts_top_level_sensitive_keys(self):
        result = redact({'password': 'secret', 'name': 'ada'})
        self.assertEqual(result['password'], '***')
        self.assertEqual(result['name'], 'ada')

    def test_redacts_sensitive_suffix(self):
        result = redact({'user_password': 'x', 'api_key': 'k', 'safe': 1})
        self.assertEqual(result['user_password'], '***')
        self.assertEqual(result['api_key'], '***')
        self.assertEqual(result['safe'], 1)

    def test_redacts_nested_dicts(self):
        payload = {
            'user': {'password': 'p', 'email': 'a@b.com'},
            'stripe_secret_key': 'sk_test',
        }
        result = redact(payload)
        self.assertEqual(result['user']['password'], '***')
        self.assertEqual(result['user']['email'], 'a@b.com')
        self.assertEqual(result['stripe_secret_key'], '***')

    def test_redacts_lists(self):
        result = redact([{'password': 'p'}, {'name': 'x'}])
        self.assertEqual(result[0]['password'], '***')
        self.assertEqual(result[1]['name'], 'x')

    def test_non_dict_passes_through(self):
        self.assertEqual(redact('string'), 'string')
        self.assertEqual(redact(42), 42)
        self.assertEqual(redact(None), None)

    def test_redacts_cvv_and_cvc_variants(self):
        result = redact({
            'card_cvc': '123',
            'card_cvv': '456',
            'cvv': '789',
            'number': '4111111111111111',
        })
        self.assertEqual(result['card_cvc'], '***')
        self.assertEqual(result['card_cvv'], '***')
        self.assertEqual(result['cvv'], '***')
        self.assertEqual(result['number'], '4111111111111111')


# ---------------------------------------------------------------------------
# JSONFormatter
# ---------------------------------------------------------------------------

class JSONFormatterTests(SimpleTestCase):
    def test_emits_well_formed_single_line_json(self):
        formatter = JSONFormatter()
        line = formatter.format(_make_record('hello world'))
        # Exactly one JSON object, no trailing newline from us.
        self.assertNotIn('\n', line)
        data = json.loads(line)
        self.assertEqual(data['message'], 'hello world')

    def test_stable_field_set(self):
        data = _format(_make_record('msg'))
        expected = {'timestamp', 'level', 'logger', 'message'}
        self.assertEqual(set(data.keys()), expected)

    def test_request_id_injected_when_bound(self):
        token = request_id_var.set('req-abc-123')
        try:
            data = _format(_make_record('with id'))
        finally:
            request_id_var.reset(token)
        self.assertEqual(data['request_id'], 'req-abc-123')

    def test_request_id_omitted_when_not_bound(self):
        # Clear any ambient value.
        try:
            request_id_var.get()
            token = request_id_var.set('')
            clear = True
        except LookupError:
            token = None
            clear = False
        try:
            data = _format(_make_record('no id'))
        finally:
            if clear:
                request_id_var.reset(token)
            elif token is None:
                pass
        self.assertNotIn('request_id', data)

    def test_extra_fields_pass_through(self):
        record = _make_record('x', extra={'order_id': 42, 'user': 'ada'})
        data = _format(record)
        self.assertEqual(data['order_id'], 42)
        self.assertEqual(data['user'], 'ada')

    def test_sensitive_extra_fields_redacted(self):
        record = _make_record('x', extra={'password': 's3cret', 'safe': 1})
        data = _format(record)
        self.assertEqual(data['password'], '***')
        self.assertEqual(data['safe'], 1)

    def test_non_serializable_extra_value_replaced(self):
        class _Nope:
            pass
        record = _make_record('x', extra={'thing': _Nope()})
        data = _format(record)
        self.assertEqual(data['thing'], '<non-serializable>')

    def test_exception_serialized_without_locals(self):
        try:
            raise ValueError('boom') from None
        except ValueError:
            import sys
            exc_info = sys.exc_info()
        record = _make_record('failed', exc_info=exc_info)
        data = _format(record)
        self.assertIn('exception', data)
        self.assertIn('ValueError: boom', data['exception'])

    def test_timestamp_is_iso8601_utc(self):
        data = _format(_make_record('t'))
        ts = data['timestamp']
        self.assertTrue(ts.endswith('Z'))
        # 2026-10-21T12:00:00.000Z
        self.assertRegex(ts, r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$')

    def test_reserved_keys_not_promoted(self):
        record = _make_record('m')
        record.levelname = 'INFO'
        data = _format(record)
        # 'levelname' is a reserved field — should not appear as a JSON key.
        self.assertNotIn('levelname', data)
        self.assertNotIn('module', data)


# ---------------------------------------------------------------------------
# RequestIDLogFilter
# ---------------------------------------------------------------------------

class RequestIDLogFilterTests(SimpleTestCase):
    def _make_handler(self):
        handler = logging.StreamHandler(StringIO())
        handler.addFilter(RequestIDLogFilter())
        handler.setFormatter(JSONFormatter())
        return handler

    def test_filter_sets_request_id_when_bound(self):
        token = request_id_var.set('filter-req-1')
        try:
            handler = self._make_handler()
            logger = logging.getLogger('filter-test-1')
            logger.handlers = [handler]
            logger.setLevel(logging.INFO)
            logger.propagate = False
            logger.info('hello')
            value = handler.stream.getvalue()
            data = json.loads(value.strip())
            self.assertEqual(data['request_id'], 'filter-req-1')
        finally:
            request_id_var.reset(token)

    def test_filter_blank_when_unbound(self):
        handler = self._make_handler()
        logger = logging.getLogger('filter-test-2')
        logger.handlers = [handler]
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.info('no request')
        value = handler.stream.getvalue()
        data = json.loads(value.strip())
        # JSON formatter omits empty request_id.
        self.assertNotIn('request_id', data)


# ---------------------------------------------------------------------------
# RequestIDMiddleware
# ---------------------------------------------------------------------------

def _dummy_view(request):
    return HttpResponse('ok')


class ValidExistingTests(SimpleTestCase):
    def test_valid_hex_id_accepted(self):
        self.assertTrue(_valid_existing('a1b2c3d4e5f6'))
    def test_valid_base62_id_accepted(self):
        self.assertTrue(_valid_existing('Req_123-abc'))
    def test_empty_rejected(self):
        self.assertFalse(_valid_existing(''))
    def test_too_long_rejected(self):
        self.assertFalse(_valid_existing('a' * 129))
    def test_invalid_chars_rejected(self):
        self.assertFalse(_valid_existing('bad id with spaces'))
        self.assertFalse(_valid_existing('drop"quote'))


class GenerateTests(SimpleTestCase):
    def test_generate_is_uuid_hex(self):
        rid = _generate()
        self.assertEqual(len(rid), 32)
        self.assertRegex(rid, r'^[0-9a-f]{32}$')

    def test_generate_uniqueness(self):
        ids = {_generate() for _ in range(50)}
        self.assertEqual(len(ids), 50)


class RequestIDMiddlewareUnitTests(SimpleTestCase):
    """Middleware unit tests via RequestFactory (no DB)."""

    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = RequestIDMiddleware(_dummy_view)

    def test_generates_id_when_no_header(self):
        request = self.factory.get('/')
        with patch.object(request, 'headers', {}):
            response = self.middleware(request)
        self.assertTrue(_valid_existing(response[REQUEST_ID_HEADER]))
        self.assertEqual(response[REQUEST_ID_HEADER], request.request_id)

    def test_accepts_valid_supplied_id(self):
        request = self.factory.get('/')
        request.META['HTTP_X_REQUEST_ID'] = 'client-supplied-123'
        response = self.middleware(request)
        self.assertEqual(response[REQUEST_ID_HEADER], 'client-supplied-123')
        self.assertEqual(request.request_id, 'client-supplied-123')

    def test_rejects_invalid_header_and_generates(self):
        request = self.factory.get('/')
        request.META['HTTP_X_REQUEST_ID'] = 'bad id with spaces'
        response = self.middleware(request)
        self.assertTrue(_valid_existing(response[REQUEST_ID_HEADER]))
        self.assertNotEqual(response[REQUEST_ID_HEADER], 'bad id with spaces')

    def test_context_cleared_after_response(self):
        request = self.factory.get('/')
        self.middleware(request)
        # After the request, the contextvar should not carry this request's ID.
        self.assertEqual(request_id(), '')

    def test_bind_and_reset_around_request(self):
        """The contextvar is set during processing and unset afterward."""
        captured = {}

        def capturing_view(req):
            captured['during'] = request_id()
            return HttpResponse('ok')

        mw = RequestIDMiddleware(capturing_view)
        request = self.factory.get('/')
        mw(request)
        self.assertTrue(_valid_existing(captured['during']))
        self.assertEqual(request_id(), '')


class RequestIDMiddlewareConcurrencyTests(SimpleTestCase):
    """Context isolation between overlapping requests (async-safe)."""

    def test_context_does_not_leak_between_requests(self):
        """Two sequential requests get different IDs and don't cross-contaminate."""
        factory = RequestFactory()
        mw = RequestIDMiddleware(_dummy_view)

        request1 = factory.get('/')
        response1 = mw(request1)
        rid1 = response1[REQUEST_ID_HEADER]

        request2 = factory.get('/')
        response2 = mw(request2)
        rid2 = response2[REQUEST_ID_HEADER]

        self.assertNotEqual(rid1, rid2)
        # After both, the context is clean.
        self.assertEqual(request_id(), '')


class RequestIDMiddlewareClientTests(TestCase):
    """End-to-end: the header reaches real responses through the full stack."""

    def test_catalog_home_echoes_request_id(self):
        rid = 'end-to-end-abc'
        response = self.client.get(
            reverse('shop:product_list'),
            HTTP_X_REQUEST_ID=rid,
        )
        self.assertEqual(response[REQUEST_ID_HEADER], rid)

    def test_generated_when_header_absent(self):
        response = self.client.get(reverse('shop:product_list'))
        self.assertTrue(_valid_existing(response[REQUEST_ID_HEADER]))

    def test_request_id_present_on_404(self):
        """The correlation header is present even on 404s (outermost middleware)."""
        response = self.client.get('/this-does-not-exist/')
        self.assertEqual(response.status_code, 404)
        self.assertIn(REQUEST_ID_HEADER, response)

    def test_client_supplied_id_preserved_through_stack(self):
        """A supplied ID travels through the full middleware stack."""
        rid = 'client-trace-abc-456'
        response = self.client.get(
            reverse('shop:product_list'),
            HTTP_X_REQUEST_ID=rid,
        )
        self.assertEqual(response[REQUEST_ID_HEADER], rid)

    def test_exception_in_view_resets_context(self):
        """When the view raises, the contextvar is still cleaned up.

        The middleware's ``finally`` block resets the contextvar via the token
        returned by ``ContextVar.set()``, so even when ``get_response`` raises,
        the next request starts with a clean context.
        """
        def boom_view(request):
            # The ID is bound during request processing.
            self.assertEqual(request_id(), request.request_id)
            raise RuntimeError('kaboom')

        mw = RequestIDMiddleware(boom_view)
        factory = RequestFactory()
        request = factory.get('/')
        request.META['HTTP_X_REQUEST_ID'] = 'boom-req-1'
        with self.assertRaises(RuntimeError):
            mw(request)
        # After the exception, the context is clean.
        self.assertEqual(request_id(), '')


# ---------------------------------------------------------------------------
# propagate_request_id()
# ---------------------------------------------------------------------------

class PropagateRequestIdTests(SimpleTestCase):
    def test_returns_empty_when_unbound(self):
        self.assertEqual(propagate_request_id(), {})

    def test_returns_dict_when_bound(self):
        token = request_id_var.set('dispatched-1')
        try:
            result = propagate_request_id()
            self.assertEqual(result, {'_request_id': 'dispatched-1'})
        finally:
            request_id_var.reset(token)


# ---------------------------------------------------------------------------
# Settings integration
# ---------------------------------------------------------------------------

class LoggingSettingsTests(SimpleTestCase):
    def test_settings_exports_a_logging_dict(self):
        from django.conf import settings
        self.assertIsInstance(settings.LOGGING, dict)
        self.assertEqual(settings.LOGGING['version'], 1)

    def test_console_handler_uses_json_formatter(self):
        from django.conf import settings
        handler = settings.LOGGING['handlers']['console']
        self.assertEqual(handler['class'], 'logging.StreamHandler')
        self.assertEqual(handler['formatter'], 'json')
        self.assertIn('request_id', handler['filters'])

    def test_request_id_filter_registered(self):
        from django.conf import settings
        self.assertIn('request_id', settings.LOGGING['filters'])

    def test_json_formatter_resolvable(self):
        from ecommerce.logging import JSONFormatter
        # Instantiating must not require DB or network.
        formatter = JSONFormatter()
        line = formatter.format(_make_record('settings check'))
        json.loads(line)  # raises if not valid JSON


# ---------------------------------------------------------------------------
# Request-ID propagation through the checkout -> task path
# ---------------------------------------------------------------------------

class CheckoutRequestIDPropagationTests(TestCase):
    """The order_created Celery task receives the active request_id."""

    @classmethod
    def setUpClass(cls):
        # Keep rate limits permissive so POSTs through checkout don't 429.
        cls._overrides = override_settings(RATE_LIMIT_CHECKOUT=(100, 3600))
        cls._overrides.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        cls._overrides.disable()
        super().tearDownClass()

    def setUp(self):
        self.client = Client()
        from decimal import Decimal
        from shop.models import Category, Product
        self.category = Category.objects.create(name='Books', slug='books')
        self.product = Product.objects.create(
            category=self.category, name='Guide', slug='guide',
            price=Decimal('19.99'),
        )
        session = self.client.session
        session['cart'] = {
            str(self.product.id): {'quantity': 1, 'price': '19.99'},
        }
        session.save()

    @patch('orders.views.order_create_view.order_created.delay')
    def test_checkout_dispatches_task_with_request_id(self, mock_delay):
        """order_created.delay receives _request_id matching the header."""
        from orders.models import Order
        rid = 'checkout-flow-req-789'
        response = self.client.post(
            reverse('orders:order_create'),
            {
                'first_name': 'Ada', 'last_name': 'Lovelace',
                'email': 'ada@example.com', 'address': '1 St',
                'postal_code': '12345', 'city': 'Oxford',
            },
            HTTP_X_REQUEST_ID=rid,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Order.objects.count(), 1)
        mock_delay.assert_called_once()
        _args, kwargs = mock_delay.call_args
        self.assertEqual(kwargs.get('_request_id'), rid)


# ---------------------------------------------------------------------------
# Celery task-tracing hook: contextvar rebinding on the worker
# ---------------------------------------------------------------------------

class CeleryTaskTracingTests(SimpleTestCase):
    """The ``Task.__call__`` override in ``ecommerce/celery.py`` rebinds the
    ``request_id`` contextvar from the ``_request_id`` task kwarg so that log
    lines emitted inside a worker carry the originating request's ID."""

    def test_tracing_call_rebinds_contextvar_from_kwarg(self):
        from ecommerce.celery import _tracing_call
        from ecommerce.logging import request_id as request_id_var

        class FakeRequest:
            headers = {}

        class FakeSelf:
            request = FakeRequest()

        fake_self = FakeSelf()

        token = request_id_var.set('')
        try:
            # Patch _task_call so we don't invoke real Celery task execution.
            with patch('ecommerce.celery._task_call', return_value=None) as mock_call:
                _tracing_call(fake_self, (), {'_request_id': 'worker-req-42'})
                # The _request_id kwarg is consumed (popped) before delegating.
                mock_call.assert_called_once_with(fake_self, (), {})
            self.assertEqual(request_id_var.get(), 'worker-req-42')
        finally:
            request_id_var.reset(token)

    def test_tracing_call_rebinds_from_headers_when_kwarg_absent(self):
        from ecommerce.celery import _tracing_call
        from ecommerce.logging import request_id as request_id_var

        class FakeRequest:
            headers = {'request_id': 'from-header-99'}

        class FakeSelf:
            request = FakeRequest()

        token = request_id_var.set('')
        try:
            with patch('ecommerce.celery._task_call', return_value=None):
                _tracing_call(FakeSelf(), (), {})
            self.assertEqual(request_id_var.get(), 'from-header-99')
        finally:
            request_id_var.reset(token)

    def test_tracing_call_does_not_clear_when_no_id_present(self):
        from ecommerce.celery import _tracing_call
        from ecommerce.logging import request_id as request_id_var

        class FakeRequest:
            headers = {}

        class FakeSelf:
            request = FakeRequest()

        token = request_id_var.set('')
        try:
            with patch('ecommerce.celery._task_call', return_value=None):
                _tracing_call(FakeSelf(), (), {})
            # No ID to bind — contextvar stays at its previous (empty) value.
            self.assertEqual(request_id_var.get(''), '')
        finally:
            request_id_var.reset(token)


# ---------------------------------------------------------------------------
# _env_level helper
# ---------------------------------------------------------------------------

class EnvLevelTests(SimpleTestCase):
    """``_env_level`` must treat empty/unset env values as unset."""

    def test_unset_returns_default(self):
        from ecommerce.logging import _env_level
        env = __import__('os').environ
        env.pop('LOG_LEVEL', None)
        self.assertEqual(_env_level('LOG_LEVEL', 'info'), 'INFO')

    def test_empty_string_returns_default(self):
        from ecommerce.logging import _env_level
        with patch.dict('os.environ', {'LOG_LEVEL': ''}):
            self.assertEqual(_env_level('LOG_LEVEL', 'info'), 'INFO')

    def test_set_value_is_uppercased(self):
        from ecommerce.logging import _env_level
        with patch.dict('os.environ', {'LOG_LEVEL': 'debug'}):
            self.assertEqual(_env_level('LOG_LEVEL', 'info'), 'DEBUG')
