from types import SimpleNamespace
from unittest.mock import patch

import stripe
from django.test import Client, TestCase, override_settings
from orders.models import Order


def _checkout_completed_event(order_id: int, payment_intent: str = 'pi_test_123'):
    session = SimpleNamespace(
        mode='payment',
        payment_status='paid',
        client_reference_id=str(order_id),
        payment_intent=payment_intent,
    )
    data = SimpleNamespace(object=session)
    return SimpleNamespace(type='checkout.session.completed', data=data)


@override_settings(STRIPE_WEBHOOK_SECRET='whsec_test_secret')
@patch('payment.views.stripe_webhook_view.payment_completed.delay')
class StripeWebhookTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.order = Order.objects.create(
            first_name='Test',
            last_name='User',
            email='t@example.com',
            address='123 St',
            postal_code='12345',
            city='City',
            paid=False,
        )
        self.url = '/payment/webhook/'

    def test_missing_signature_returns_400(self, _mock_task):
        response = self.client.post(self.url, b'{}', content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_invalid_signature_returns_400(self, mock_task):
        with patch(
            'stripe.Webhook.construct_event',
            side_effect=stripe.error.SignatureVerificationError('bad', 'sig'),
        ):
            response = self.client.post(
                self.url,
                b'payload',
                content_type='application/json',
                HTTP_STRIPE_SIGNATURE='v1,evil',
            )
        self.assertEqual(response.status_code, 400)
        mock_task.assert_not_called()

    def test_invalid_payload_returns_400(self, mock_task):
        with patch(
            'stripe.Webhook.construct_event',
            side_effect=ValueError('invalid json'),
        ):
            response = self.client.post(
                self.url,
                b'not-json',
                content_type='application/json',
                HTTP_STRIPE_SIGNATURE='v1,ok',
            )
        self.assertEqual(response.status_code, 400)
        mock_task.assert_not_called()

    def test_checkout_session_completed_marks_order_paid_and_enqueues_task(self, mock_task):
        event = _checkout_completed_event(self.order.id)
        with patch('stripe.Webhook.construct_event', return_value=event):
            response = self.client.post(
                self.url,
                b'{"type": "ignored"}',
                content_type='application/json',
                HTTP_STRIPE_SIGNATURE='v1,test',
            )
        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertTrue(self.order.paid)
        self.assertEqual(self.order.stripe_id, 'pi_test_123')
        mock_task.assert_called_once_with(self.order.id)

    def test_non_numeric_client_reference_returns_400(self, mock_task):
        session = SimpleNamespace(
            mode='payment',
            payment_status='paid',
            client_reference_id='not-an-order-id',
            payment_intent='pi_x',
        )
        event = SimpleNamespace(
            type='checkout.session.completed',
            data=SimpleNamespace(object=session),
        )
        with patch('stripe.Webhook.construct_event', return_value=event):
            response = self.client.post(
                self.url,
                b'{}',
                content_type='application/json',
                HTTP_STRIPE_SIGNATURE='v1,test',
            )
        self.assertEqual(response.status_code, 400)
        mock_task.assert_not_called()

    def test_unknown_order_reference_returns_400(self, mock_task):
        event = _checkout_completed_event(order_id=99999)
        with patch('stripe.Webhook.construct_event', return_value=event):
            response = self.client.post(
                self.url,
                b'{}',
                content_type='application/json',
                HTTP_STRIPE_SIGNATURE='v1,test',
            )
        self.assertEqual(response.status_code, 400)
        mock_task.assert_not_called()

    def test_non_payment_session_does_not_update_order(self, mock_task):
        session = SimpleNamespace(
            mode='setup',
            payment_status='paid',
            client_reference_id=str(self.order.id),
            payment_intent='pi_x',
        )
        event = SimpleNamespace(
            type='checkout.session.completed',
            data=SimpleNamespace(object=session),
        )
        with patch('stripe.Webhook.construct_event', return_value=event):
            response = self.client.post(
                self.url,
                b'{}',
                content_type='application/json',
                HTTP_STRIPE_SIGNATURE='v1,test',
            )
        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertFalse(self.order.paid)
        mock_task.assert_not_called()
