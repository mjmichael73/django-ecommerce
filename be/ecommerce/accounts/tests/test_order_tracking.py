"""Tests for the public order-tracking lookup (card ee76485f).

Covers:
  * Authenticated + anonymous access to GET (form render).
  * Valid lookup (paid order, correct email) renders the status.
  * Invalid order id, unknown order, unpaid order, email mismatch all return
    the same "not found" message — no enumeration of unpaid orders.
  * The lookup is case-insensitive on the billing email.
  * Security: an unpaid order must never be shown to a guesser, even with the
    right email; a paid order with the wrong email is rejected.
  * Rate limiting on the POST path (tracking scope).
  * The Stripe webhook flips an order's status to PAID on first completion and
    is idempotent across replays.
"""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from orders.models import Order

User = get_user_model()


def _make_order(email='ada@example.com', paid=True, status=None, **kwargs):
    defaults = dict(
        first_name='Ada',
        last_name='Lovelace',
        email=email,
        address='1 Analytical Engine Rd',
        postal_code='OX1',
        city='Oxford',
        paid=paid,
    )
    if status is not None:
        defaults['status'] = status
    defaults.update(kwargs)
    return Order.objects.create(**defaults)


def _checkout_completed_event(order_id, payment_intent='pi_test_123'):
    session = SimpleNamespace(
        mode='payment',
        payment_status='paid',
        client_reference_id=str(order_id),
        payment_intent=payment_intent,
    )
    data = SimpleNamespace(object=session)
    return SimpleNamespace(type='checkout.session.completed', data=data)


@override_settings(STRIPE_WEBHOOK_SECRET='whsec_test_secret')
class OrderTrackViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        # The LocMem cache is per-process and persists across tests in this
        # class — clear it so rate-limit counters do not leak between tests.
        from django.core.cache import cache
        cache.clear()
        self.url = '/accounts/tracking/'
        self.user = User.objects.create_user(
            'buyer', 'buyer@example.com', 'pw12345',
            first_name='Sam', last_name='Shopper',
        )
        self.paid_order = _make_order(
            email='paid@example.com', paid=True, status=Order.Status.PAID,
        )
        self.unpaid_order = _make_order(
            email='unpaid@example.com', paid=False, status=Order.Status.PENDING,
        )

    # -- GET ---------------------------------------------------------------

    def test_get_renders_form(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Track your order')
        self.assertContains(resp, 'name="order_id"')
        self.assertContains(resp, 'name="email"')

    def test_get_for_anonymous_user(self):
        # Public view: no login required.
        # Clear session so there's no session key yet, then GET.
        self.client.session.flush()
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)

    def test_get_for_authenticated_user(self):
        self.client.force_login(self.user)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)

    # -- POST: valid lookups ----------------------------------------------

    def test_valid_paid_order_lookup(self):
        resp = self.client.post(self.url, {
            'order_id': str(self.paid_order.id),
            'email': 'paid@example.com',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, f'Order #{self.paid_order.id}')
        self.assertContains(resp, 'Paid')
        self.assertEqual(resp.context['order'], self.paid_order)

    def test_lookup_is_case_insensitive_on_email(self):
        resp = self.client.post(self.url, {
            'order_id': str(self.paid_order.id),
            'email': 'PAID@EXAMPLE.COM',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Paid')

    def test_lookup_with_whitespace_is_trimmed(self):
        resp = self.client.post(self.url, {
            'order_id': f' {self.paid_order.id} ',
            'email': '  paid@example.com  ',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Paid')

    # -- POST: invalid lookups --------------------------------------------

    def test_unknown_order_id_returns_not_found(self):
        resp = self.client.post(self.url, {
            'order_id': '999999',
            'email': 'paid@example.com',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'No paid order found')
        self.assertIsNone(resp.context['order'])
        self.assertIsNotNone(resp.context['error'])

    def test_non_numeric_order_id_returns_form_error(self):
        resp = self.client.post(self.url, {
            'order_id': 'abc',
            'email': 'paid@example.com',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'valid order number')
        self.assertIsNone(resp.context['order'])

    def test_email_mismatch_returns_not_found(self):
        # Correct id, wrong email — must not reveal the order exists.
        resp = self.client.post(self.url, {
            'order_id': str(self.paid_order.id),
            'email': 'someone-else@example.com',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'No paid order found')
        self.assertIsNone(resp.context['order'])

    # -- Security boundary: unpaid orders are never exposed ---------------

    def test_unpaid_order_not_exposed_even_with_correct_email(self):
        resp = self.client.post(self.url, {
            'order_id': str(self.unpaid_order.id),
            'email': 'unpaid@example.com',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'No paid order found')
        self.assertIsNone(resp.context['order'])
        # The order's email must never appear in the response.
        self.assertNotIn('unpaid@example.com', resp.content.decode())

    def test_unpaid_order_id_does_not_leak_existence(self):
        # Same response for an unpaid vs a non-existent order id.
        unpaid = self.client.post(self.url, {
            'order_id': str(self.unpaid_order.id),
            'email': 'unpaid@example.com',
        })
        missing = self.client.post(self.url, {
            'order_id': '999999',
            'email': 'unpaid@example.com',
        })
        self.assertIn('No paid order found', unpaid.content.decode())
        self.assertIn('No paid order found', missing.content.decode())

    # -- Rate limiting -----------------------------------------------------

    @override_settings(RATE_LIMIT_TRACKING=(3, 3600))
    def test_post_is_rate_limited(self):
        for _ in range(3):
            resp = self.client.post(self.url, {
                'order_id': str(self.paid_order.id),
                'email': 'paid@example.com',
            })
            self.assertEqual(resp.status_code, 200)
        # 4th POST within the window is rejected.
        resp = self.client.post(self.url, {
            'order_id': str(self.paid_order.id),
            'email': 'paid@example.com',
        })
        self.assertEqual(resp.status_code, 429)
        self.assertIn('Retry-After', resp)

    @override_settings(RATE_LIMIT_TRACKING=(0, 0))
    def test_post_rate_limit_can_be_disabled(self):
        # period == 0 disables rate limiting (per rate_limit._limit).
        for _ in range(50):
            resp = self.client.post(self.url, {
                'order_id': str(self.unpaid_order.id),
                'email': 'unpaid@example.com',
            })
            self.assertEqual(resp.status_code, 200)

    def test_get_does_not_consume_rate_limit_quota(self):
        for _ in range(50):
            resp = self.client.get(self.url)
            self.assertEqual(resp.status_code, 200)
        # POST quota still intact.
        resp = self.client.post(self.url, {
            'order_id': str(self.paid_order.id),
            'email': 'paid@example.com',
        })
        self.assertEqual(resp.status_code, 200)


@override_settings(STRIPE_WEBHOOK_SECRET='whsec_test_secret')
@patch('orders.views.order_create_view.order_created.delay')
@patch('payment.views.stripe_webhook_view.payment_completed.delay')
class OrderTrackWebhookStatusTests(TestCase):
    """The webhook advances status to PAID exactly once (idempotent)."""

    def setUp(self):
        self.client = Client()
        self.order = Order.objects.create(
            first_name='Ada', last_name='Lovelace',
            email='ada@example.com', address='1 St', postal_code='1', city='C',
            paid=False, status=Order.Status.PENDING,
        )

    def _deliver(self, mock_pay_task, mock_order_task):
        from types import SimpleNamespace
        event = _checkout_completed_event(self.order.id)
        with patch('stripe.Webhook.construct_event', return_value=event):
            return self.client.post(
                '/payment/webhook/',
                b'{"type": "checkout.session.completed"}',
                content_type='application/json',
                HTTP_STRIPE_SIGNATURE='v1,test',
            )

    def test_webhook_sets_paid_and_status(
        self, mock_pay_task, mock_order_task
    ):
        resp = self._deliver(mock_pay_task, mock_order_task)
        self.assertEqual(resp.status_code, 200)
        self.order.refresh_from_db()
        self.assertTrue(self.order.paid)
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertEqual(self.order.stripe_id, 'pi_test_123')
        mock_pay_task.assert_called_once_with(self.order.id)

    def test_webhook_replay_does_not_re_transition_status(
        self, mock_pay_task, mock_order_task
    ):
        self._deliver(mock_pay_task, mock_order_task)
        # Replay — the guarded UPDATE returns 0 (paid=False is false now), so
        # the invoice task must NOT fire again.
        resp = self._deliver(mock_pay_task, mock_order_task)
        self.assertEqual(resp.status_code, 200)
        mock_pay_task.assert_called_once_with(self.order.id)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)

    def test_paid_order_appears_in_tracking(
        self, mock_pay_task, mock_order_task
    ):
        self._deliver(mock_pay_task, mock_order_task)
        self.order.refresh_from_db()
        resp = self.client.post('/accounts/tracking/', {
            'order_id': str(self.order.id),
            'email': 'ada@example.com',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, f'Order #{self.order.id}')
        self.assertContains(resp, 'Paid')

    def test_unpaid_order_invisible_in_tracking_before_payment(
        self, mock_pay_task, mock_order_task
    ):
        resp = self.client.post('/accounts/tracking/', {
            'order_id': str(self.order.id),
            'email': 'ada@example.com',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'No paid order found')
        self.assertNotIn(f'Order #{self.order.id}', resp.content.decode())
