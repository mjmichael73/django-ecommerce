"""Refund handling tests (card a3cg42c4).

``stripe.Refund.create`` is mocked everywhere — the suite never talks to the
Stripe API.
"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import stripe
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from orders.models import Order, OrderItem
from shop.models import Category, Product

from payment.refunds import refund_order
from payment.tests.test_stripe_webhook import _checkout_completed_event


def _product(price='100.00'):
    category, _ = Category.objects.get_or_create(
        slug='books',
        defaults={'name': 'Books'},
    )
    product, _ = Product.objects.get_or_create(
        slug='guide',
        defaults={
            'category': category,
            'name': 'Guide',
            'price': Decimal(price),
        },
    )
    return product


def _order(paid=True, stripe_id='pi_test_123', price='100.00', quantity=1):
    order = Order.objects.create(
        first_name='Test',
        last_name='User',
        email='t@example.com',
        address='123 St',
        postal_code='12345',
        city='City',
        paid=paid,
        stripe_id=stripe_id or '',
    )
    OrderItem.objects.create(
        order=order,
        product=_product(price),
        price=Decimal(price),
        quantity=quantity,
    )
    return order


def _charge_refunded_event(payment_intent='pi_test_123', amount_refunded=1050,
                           refunds=None):
    charge = SimpleNamespace(
        payment_intent=payment_intent,
        amount_refunded=amount_refunded,
        refunds=SimpleNamespace(
            data=refunds if refunds is not None else [SimpleNamespace(id='re_1')]
        ),
    )
    return SimpleNamespace(
        type='charge.refunded',
        data=SimpleNamespace(object=charge),
    )


class RefundGuardsTests(TestCase):
    """Every guard refuses *before* Stripe is called."""

    @patch('stripe.Refund.create')
    def test_unpaid_order_is_not_refundable(self, mock_create):
        order = _order(paid=False, stripe_id='pi_test_123')

        ok, reason = refund_order(order)

        self.assertFalse(ok)
        self.assertEqual(reason, 'not refundable')
        mock_create.assert_not_called()

    @patch('stripe.Refund.create')
    def test_paid_order_without_stripe_id_is_not_refundable(self, mock_create):
        order = _order(paid=True, stripe_id='')

        ok, reason = refund_order(order)

        self.assertFalse(ok)
        self.assertEqual(reason, 'not refundable')
        mock_create.assert_not_called()

    @patch('stripe.Refund.create')
    def test_zero_amount_is_invalid(self, mock_create):
        order = _order()

        ok, reason = refund_order(order, Decimal('0'))

        self.assertFalse(ok)
        self.assertEqual(reason, 'invalid amount')
        mock_create.assert_not_called()

    @patch('stripe.Refund.create')
    def test_negative_amount_is_invalid(self, mock_create):
        order = _order()

        ok, reason = refund_order(order, Decimal('-5.00'))

        self.assertFalse(ok)
        self.assertEqual(reason, 'invalid amount')
        mock_create.assert_not_called()

    @patch('stripe.Refund.create')
    def test_non_numeric_amount_is_invalid(self, mock_create):
        order = _order()

        ok, reason = refund_order(order, 'not-a-number')

        self.assertFalse(ok)
        self.assertEqual(reason, 'invalid amount')
        mock_create.assert_not_called()

    @patch('stripe.Refund.create')
    def test_locally_fully_refunded_order_is_refused(self, mock_create):
        order = _order()
        order.refunded_amount = order.get_total_cost()
        order.save(update_fields=['refunded_amount'])

        ok, reason = refund_order(order)

        self.assertFalse(ok)
        self.assertEqual(reason, 'already refunded')
        mock_create.assert_not_called()


class RefundOrderTests(TestCase):
    """The happy paths and the error contract of ``refund_order``."""

    @patch('stripe.Refund.create')
    def test_full_refund_refunds_the_whole_total(self, mock_create):
        mock_create.return_value = SimpleNamespace(id='re_full')
        order = _order()

        ok, reason = refund_order(order)

        self.assertTrue(ok)
        self.assertEqual(reason, 'ok')
        mock_create.assert_called_once_with(payment_intent='pi_test_123')
        order.refresh_from_db()
        self.assertEqual(order.refunded_amount, Decimal('100.00'))
        self.assertEqual(order.refund_id, 're_full')
        self.assertIsNotNone(order.refunded_at)

    @patch('stripe.Refund.create')
    def test_full_refund_after_a_partial_refunds_only_the_rest(self, mock_create):
        mock_create.return_value = SimpleNamespace(id='re_full')
        order = _order()
        order.refunded_amount = Decimal('10.00')
        order.save(update_fields=['refunded_amount'])

        ok, _reason = refund_order(order)

        self.assertTrue(ok)
        mock_create.assert_called_once_with(payment_intent='pi_test_123')
        order.refresh_from_db()
        self.assertEqual(order.refunded_amount, Decimal('100.00'))

    @patch('stripe.Refund.create')
    def test_partial_refund_converts_dollars_to_cents(self, mock_create):
        mock_create.return_value = SimpleNamespace(id='re_partial')
        order = _order()

        ok, reason = refund_order(order, Decimal('10.50'))

        self.assertTrue(ok)
        self.assertEqual(reason, 'ok')
        mock_create.assert_called_once_with(
            payment_intent='pi_test_123',
            amount=1050,
        )
        order.refresh_from_db()
        self.assertEqual(order.refunded_amount, Decimal('10.50'))
        self.assertEqual(order.refund_id, 're_partial')

    @patch('stripe.Refund.create')
    def test_partial_refunds_accumulate(self, mock_create):
        mock_create.side_effect = [
            SimpleNamespace(id='re_1'),
            SimpleNamespace(id='re_2'),
        ]
        order = _order()

        ok1, _ = refund_order(order, Decimal('10.00'))
        ok2, _ = refund_order(order, Decimal('5.50'))

        self.assertTrue(ok1)
        self.assertTrue(ok2)
        self.assertEqual(mock_create.call_count, 2)
        order.refresh_from_db()
        self.assertEqual(order.refunded_amount, Decimal('15.50'))
        self.assertEqual(order.refund_id, 're_2')

    @patch('stripe.Refund.create')
    def test_float_amount_is_converted_to_cents(self, mock_create):
        mock_create.return_value = SimpleNamespace(id='re_float')
        order = _order()

        ok, _reason = refund_order(order, 10.5)

        self.assertTrue(ok)
        mock_create.assert_called_once_with(
            payment_intent='pi_test_123',
            amount=1050,
        )

    @patch('stripe.Refund.create')
    def test_stripe_error_is_returned_not_raised(self, mock_create):
        mock_create.side_effect = stripe.error.StripeError('boom')
        order = _order()

        ok, reason = refund_order(order)

        self.assertFalse(ok)
        self.assertTrue(reason.startswith('stripe error:'))
        self.assertIn('boom', reason)
        order.refresh_from_db()
        self.assertEqual(order.refunded_amount, Decimal('0.00'))
        self.assertEqual(order.refund_id, '')
        self.assertIsNone(order.refunded_at)

    @patch('stripe.Refund.create')
    def test_card_error_is_returned_not_raised(self, mock_create):
        mock_create.side_effect = stripe.error.CardError(
            'Your card was declined.',
            'payment_method',
            'card_declined',
        )
        order = _order()

        ok, reason = refund_order(order)

        self.assertFalse(ok)
        self.assertTrue(reason.startswith('stripe error:'))
        self.assertIn('declined', reason)
        order.refresh_from_db()
        self.assertEqual(order.refunded_amount, Decimal('0.00'))

    @patch('stripe.Refund.create')
    def test_unexpected_error_is_returned_not_raised(self, mock_create):
        mock_create.side_effect = RuntimeError('kaboom')
        order = _order()

        ok, reason = refund_order(order)

        self.assertFalse(ok)
        self.assertTrue(reason.startswith('stripe error:'))
        order.refresh_from_db()
        self.assertEqual(order.refunded_amount, Decimal('0.00'))


class RefundAdminActionTests(TestCase):
    """The ``issue_full_refund`` admin action.

    The action POST answers with a redirect (302) and the messages are
    read from the request's message storage — the admin page itself is
    never rendered here (see the note in the report: rendering an admin
    page after ``accounts.tests.test_social_auth`` ran in the same
    process trips over allauth's admin registrations, which outlive the
    app-registry restore of that test).
    """

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_superuser(
            username='admin',
            email='admin@example.com',
            password='password',
        )
        self.client.force_login(self.user)
        self.url = reverse('admin:orders_order_changelist')

    def _run_action(self, order):
        response = self.client.post(
            self.url,
            {
                'action': 'issue_full_refund',
                '_selected_action': [str(order.pk)],
            },
        )
        return response, [str(m) for m in get_messages(response.wsgi_request)]

    @patch('stripe.Refund.create')
    def test_action_refunds_a_paid_order(self, mock_create):
        mock_create.return_value = SimpleNamespace(id='re_admin')
        order = _order()

        response, messages = self._run_action(order)

        self.assertEqual(response.status_code, 302)
        mock_create.assert_called_once_with(payment_intent='pi_test_123')
        order.refresh_from_db()
        self.assertEqual(order.refunded_amount, Decimal('100.00'))
        self.assertEqual(order.refund_id, 're_admin')
        self.assertIsNotNone(order.refunded_at)
        self.assertTrue(
            any('Full refund issued for 1 order(s)' in m for m in messages),
            messages,
        )

    @patch('stripe.Refund.create')
    def test_action_reports_the_reason_for_an_unpaid_order(self, mock_create):
        order = _order(paid=False)

        response, messages = self._run_action(order)

        self.assertEqual(response.status_code, 302)
        mock_create.assert_not_called()
        order.refresh_from_db()
        self.assertEqual(order.refunded_amount, Decimal('0.00'))
        self.assertTrue(any('not refundable' in m for m in messages), messages)

    @patch('stripe.Refund.create')
    def test_double_click_does_not_refund_twice(self, mock_create):
        mock_create.return_value = SimpleNamespace(id='re_admin')
        order = _order()

        self._run_action(order)
        _response, messages = self._run_action(order)

        mock_create.assert_called_once_with(payment_intent='pi_test_123')
        order.refresh_from_db()
        self.assertEqual(order.refunded_amount, Decimal('100.00'))
        self.assertTrue(any('already refunded' in m for m in messages), messages)


@override_settings(STRIPE_WEBHOOK_SECRET='whsec_test_secret')
@patch('payment.views.stripe_webhook_view.payment_completed.delay')
class RefundWebhookTests(TestCase):
    """Inbound ``charge.refunded`` events are the source of truth."""

    def setUp(self):
        self.client = Client()
        self.url = '/payment/webhook/'

    def _deliver(self, event):
        with patch('stripe.Webhook.construct_event', return_value=event):
            return self.client.post(
                self.url,
                b'{"type": "ignored"}',
                content_type='application/json',
                HTTP_STRIPE_SIGNATURE='v1,test',
            )

    @patch('stripe.Refund.create')
    def test_charge_refunded_sets_the_refunded_amount(self, mock_create, _task):
        order = _order(paid=True, stripe_id='pi_test_123')

        response = self._deliver(
            _charge_refunded_event(payment_intent='pi_test_123', amount_refunded=2500)
        )

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.refunded_amount, Decimal('25.00'))
        self.assertEqual(order.refund_id, 're_1')
        self.assertIsNotNone(order.refunded_at)
        # An inbound refund event never issues a refund itself.
        mock_create.assert_not_called()

    @patch('stripe.Refund.create')
    def test_charge_refunded_for_an_unknown_payment_intent_is_a_noop(self, mock_create, _task):
        order = _order(paid=True, stripe_id='pi_test_123')

        response = self._deliver(
            _charge_refunded_event(payment_intent='pi_unknown', amount_refunded=2500)
        )

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.refunded_amount, Decimal('0.00'))
        self.assertIsNone(order.refunded_at)
        mock_create.assert_not_called()

    @patch('stripe.Refund.create')
    def test_multiple_refunds_keep_the_refund_id_blank(self, mock_create, _task):
        order = _order(paid=True, stripe_id='pi_test_123')
        event = _charge_refunded_event(
            payment_intent='pi_test_123',
            amount_refunded=5000,
            refunds=[SimpleNamespace(id='re_1'), SimpleNamespace(id='re_2')],
        )

        response = self._deliver(event)

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.refunded_amount, Decimal('50.00'))
        self.assertEqual(order.refund_id, '')

    @patch('stripe.Refund.create')
    def test_checkout_completed_still_works_next_to_a_refund_event(self, mock_create, mock_task):
        order = _order(paid=False, stripe_id='')

        completed = self._deliver(_checkout_completed_event(order.id))
        refunded = self._deliver(
            _charge_refunded_event(payment_intent='pi_test_123', amount_refunded=1050)
        )

        self.assertEqual(completed.status_code, 200)
        self.assertEqual(refunded.status_code, 200)
        order.refresh_from_db()
        self.assertTrue(order.paid)
        self.assertEqual(order.stripe_id, 'pi_test_123')
        self.assertEqual(order.refunded_amount, Decimal('10.50'))
        mock_task.assert_called_once_with(order.id)
        mock_create.assert_not_called()
