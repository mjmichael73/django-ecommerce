from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from orders.models import Order, OrderItem
from shop.models import Category, Product

from payment.tests.test_stripe_webhook import _checkout_completed_event


def _create_order(paid=False, stripe_id=None, **overrides):
    fields = {
        'first_name': 'Test',
        'last_name': 'User',
        'email': 't@example.com',
        'address': '123 St',
        'postal_code': '12345',
        'city': 'City',
        'paid': paid,
    }
    if stripe_id is not None:
        fields['stripe_id'] = stripe_id
    fields.update(overrides)
    return Order.objects.create(**fields)


@override_settings(STRIPE_WEBHOOK_SECRET='whsec_test_secret')
@patch('payment.views.stripe_webhook_view.payment_completed.delay')
class WebhookIdempotencyTests(TestCase):
    """The webhook must be a no-op on Stripe's at-least-once redeliveries."""

    def setUp(self):
        self.client = Client()
        self.order = _create_order()
        self.url = '/payment/webhook/'

    def _deliver(self, event):
        with patch('stripe.Webhook.construct_event', return_value=event):
            return self.client.post(
                self.url,
                b'{"type": "ignored"}',
                content_type='application/json',
                HTTP_STRIPE_SIGNATURE='v1,test',
            )

    def test_replayed_event_dispatches_invoice_task_only_once(self, mock_task):
        event = _checkout_completed_event(self.order.id)

        first = self._deliver(event)
        second = self._deliver(event)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        mock_task.assert_called_once()
        _called_args, _called_kwargs = mock_task.call_args
        self.assertEqual(_called_args[0], self.order.id)
        self.assertIn('_request_id', _called_kwargs)
        self.order.refresh_from_db()
        self.assertTrue(self.order.paid)
        self.assertEqual(self.order.stripe_id, 'pi_test_123')

    def test_already_paid_order_with_same_stripe_id_is_not_reprocessed(self, mock_task):
        self.order.paid = True
        self.order.stripe_id = 'pi_test_123'
        self.order.save()

        response = self._deliver(_checkout_completed_event(self.order.id))

        self.assertEqual(response.status_code, 200)
        mock_task.assert_not_called()
        self.order.refresh_from_db()
        self.assertTrue(self.order.paid)
        self.assertEqual(self.order.stripe_id, 'pi_test_123')

    def test_first_payment_wins_over_replayed_payment_intent(self, mock_task):
        self.order.paid = True
        self.order.stripe_id = 'pi_first_writer'
        self.order.save()

        response = self._deliver(
            _checkout_completed_event(self.order.id, payment_intent='pi_replay')
        )

        self.assertEqual(response.status_code, 200)
        mock_task.assert_not_called()
        self.order.refresh_from_db()
        self.assertTrue(self.order.paid)
        self.assertEqual(self.order.stripe_id, 'pi_first_writer')

    def test_unknown_order_in_valid_event_still_returns_400(self, mock_task):
        response = self._deliver(_checkout_completed_event(order_id=99999))

        self.assertEqual(response.status_code, 400)
        mock_task.assert_not_called()


class PaymentProcessIdempotencyTests(TestCase):
    """A paid order must never reach Stripe a second time; an unpaid one
    creates its session under a deterministic per-order idempotency key."""

    def setUp(self):
        self.client = Client()
        self.url = reverse('payment:process')
        category = Category.objects.create(name='Books', slug='books')
        product = Product.objects.create(
            category=category,
            name='Guide',
            slug='guide',
            price=Decimal('19.99'),
        )
        self.order = _create_order()
        OrderItem.objects.create(
            order=self.order,
            product=product,
            price=product.price,
            quantity=1,
        )
        session = self.client.session
        session['order_id'] = self.order.id
        session.save()

    @patch('stripe.checkout.Session.create')
    def test_get_on_paid_order_redirects_without_creating_session(self, mock_create):
        self.order.paid = True
        self.order.save()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('payment:completed'))
        mock_create.assert_not_called()

    @patch('stripe.checkout.Session.create')
    def test_post_on_paid_order_redirects_without_creating_session(self, mock_create):
        self.order.paid = True
        self.order.save()

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('payment:completed'))
        mock_create.assert_not_called()

    @patch('stripe.checkout.Session.create')
    def test_post_creates_session_with_order_scoped_idempotency_key(self, mock_create):
        checkout_url = 'https://checkout.stripe.com/c/pay/cs_test_idem'
        mock_create.return_value = SimpleNamespace(url=checkout_url)

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, checkout_url)
        mock_create.assert_called_once()
        _, kwargs = mock_create.call_args
        self.assertEqual(kwargs['idempotency_key'], f'order-{self.order.id}')
        self.assertEqual(kwargs['client_reference_id'], self.order.id)
        self.assertTrue(kwargs['line_items'])
