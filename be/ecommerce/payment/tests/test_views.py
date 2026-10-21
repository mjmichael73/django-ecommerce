"""Unit tests for payment views.

Covers ``payment_process`` (Stripe Checkout session creation),
``payment_completed``, and ``payment_canceled``.
"""
from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.test import TestCase, RequestFactory, override_settings
from django.urls import reverse
from django.shortcuts import redirect

from orders.models import Order
from shop.models import Category, Product
from payment.views.payment_process_view import payment_process
from payment.views.payment_completed_view import payment_completed
from payment.views.payment_canceled_view import payment_canceled


class PaymentProcessViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(name='Test', slug='test')
        cls.product = Product.objects.create(
            category=cls.category,
            name='Guide',
            slug='guide',
            price=Decimal('19.99'),
        )

    def setUp(self):
        self.factory = RequestFactory()
        self.order = Order.objects.create(
            first_name='Test', last_name='User',
            email='t@example.com', address='123 St',
            postal_code='12345', city='City',
        )
        from orders.models import OrderItem
        OrderItem.objects.create(
            order=self.order, product=self.product,
            price=Decimal('19.99'), quantity=2,
        )

    def _request_with_session(self, method='GET', path='/', **kwargs):
        request = self.factory.generic(method, path, **kwargs)
        request.session = {'order_id': self.order.id}
        request.META['HTTP_X_REQUESTED_WITH'] = 'XMLHttpRequest'
        # build_absolute_uri
        request.build_absolute_uri = lambda url: f'http://testserver{url}'
        return request

    @patch('payment.views.payment_process_view.stripe')
    def test_get_renders_process_template(self, mock_stripe):
        """GET /payment/process renders the process.html template."""
        request = self._request_with_session()
        # Need to add a user for the template context
        request.user = MagicMock()
        request.user.is_authenticated = False
        response = payment_process(request)
        self.assertEqual(response.status_code, 200)

    @patch('payment.views.payment_process_view.stripe')
    @override_settings(STRIPE_SECRET_KEY='sk_test_123')
    def test_post_creates_stripe_session_and_redirects(self, mock_stripe):
        """POST creates a Checkout session and redirects to Stripe."""
        mock_session = MagicMock()
        mock_session.url = 'https://checkout.stripe.com/test-session'
        mock_stripe.checkout.Session.create.return_value = mock_session

        request = self._request_with_session(method='POST', data={})
        request.user = MagicMock()
        request.user.is_authenticated = False

        response = payment_process(request)
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(
            response, 'https://checkout.stripe.com/test-session',
            fetch_redirect_response=False,
        )
        # Stripe was called with idempotency_key
        args, kwargs = mock_stripe.checkout.Session.create.call_args
        self.assertEqual(kwargs['idempotency_key'], f'order-{self.order.id}')

    @patch('payment.views.payment_process_view.stripe')
    @override_settings(STRIPE_SECRET_KEY='sk_test_123')
    def test_paid_order_redirects_to_completed(self, mock_stripe):
        """A paid order redirects to the completed page instead of creating a new session."""
        self.order.paid = True
        self.order.save()

        request = self._request_with_session(method='POST', data={})
        request.user = MagicMock()
        request.user.is_authenticated = False

        response = payment_process(request)
        self.assertEqual(response.status_code, 302)
        mock_stripe.checkout.Session.create.assert_not_called()

    @patch('payment.views.payment_process_view.stripe')
    @override_settings(STRIPE_SECRET_KEY='sk_test_123')
    def test_line_items_include_variant_name_when_present(self, mock_stripe):
        """Line item name includes the variant name when the order item has a variant."""
        from orders.models import OrderItem
        from shop.models import ProductVariant
        variant = ProductVariant.objects.create(
            product=self.product, name='Red / M', sku='MUG-RED-M', price=Decimal('49.99'),
        )
        OrderItem.objects.create(
            order=self.order, product=self.product, variant=variant,
            price=Decimal('49.99'), quantity=1,
        )
        self.order.paid = False
        self.order.save()

        mock_session = MagicMock()
        mock_session.url = 'https://checkout.stripe.com/test'
        mock_stripe.checkout.Session.create.return_value = mock_session

        request = self._request_with_session(method='POST', data={})
        request.user = MagicMock()
        request.user.is_authenticated = False

        payment_process(request)
        args, kwargs = mock_stripe.checkout.Session.create.call_args
        line_items = kwargs['line_items']
        # The variant line item should mention the variant name
        variant_item = [li for li in line_items if 'Red / M' in li['price_data']['product_data']['name']]
        self.assertEqual(len(variant_item), 1)

    @patch('payment.views.payment_process_view.stripe')
    @override_settings(STRIPE_SECRET_KEY='sk_test_123')
    def test_line_items_without_variant_use_product_name(self, mock_stripe):
        """Line items without a variant use just the product name."""
        mock_session = MagicMock()
        mock_session.url = 'https://checkout.stripe.com/test'
        mock_stripe.checkout.Session.create.return_value = mock_session

        request = self._request_with_session(method='POST', data={})
        request.user = MagicMock()
        request.user.is_authenticated = False

        payment_process(request)
        args, kwargs = mock_stripe.checkout.Session.create.call_args
        line_items = kwargs['line_items']
        # No variant → product name only
        self.assertEqual(
            line_items[0]['price_data']['product_data']['name'],
            self.product.name,
        )
        # unit_amount is in cents
        self.assertEqual(
            line_items[0]['price_data']['unit_amount'],
            int(Decimal('19.99') * Decimal('100')),
        )


class PaymentCompletedCanceledViewTests(TestCase):
    """``payment_completed`` and ``payment_canceled`` render their templates."""

    def test_completed_renders_template(self):
        from django.test import Client
        response = Client().get(reverse('payment:completed'))
        self.assertEqual(response.status_code, 200)

    def test_canceled_renders_template(self):
        from django.test import Client
        response = Client().get(reverse('payment:canceled'))
        self.assertEqual(response.status_code, 200)
