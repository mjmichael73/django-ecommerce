"""Unit tests for order model methods.

Covers ``Order.get_total_cost``, ``get_total_cost_before_discount``,
``get_discount``, ``get_stripe_url``, and ``OrderItem.get_cost``.
"""
from decimal import Decimal
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase, override_settings

from orders.models import Order, OrderItem
from shop.models import Category, Product


class OrderModelMixin(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(name='Books', slug='books')
        cls.product = Product.objects.create(
            category=cls.category,
            name='Guide',
            slug='guide',
            price=Decimal('19.99'),
        )

    def _make_order(self, **kwargs):
        defaults = {
            'first_name': 'Test',
            'last_name': 'User',
            'email': 't@example.com',
            'address': '123 St',
            'postal_code': '12345',
            'city': 'City',
        }
        defaults.update(kwargs)
        return Order.objects.create(**defaults)

    def _make_item(self, order, price=None, quantity=1, product=None):
        return OrderItem.objects.create(
            order=order,
            product=product or self.product,
            price=price if price is not None else (product or self.product).price,
            quantity=quantity,
        )


class OrderTotalCostTests(OrderModelMixin):
    """``Order.get_total_cost`` and the discount pipeline."""

    def test_total_cost_without_items(self):
        order = self._make_order()
        self.assertEqual(order.get_total_cost(), Decimal('0'))

    def test_total_cost_single_item(self):
        order = self._make_order()
        self._make_item(order, price=Decimal('19.99'), quantity=2)
        self.assertEqual(order.get_total_cost_before_discount(), Decimal('39.98'))
        self.assertEqual(order.get_total_cost(), Decimal('39.98'))

    def test_total_cost_multiple_items(self):
        order = self._make_order()
        self._make_item(order, price=Decimal('10.00'), quantity=2)
        self._make_item(order, price=Decimal('5.50'), quantity=3)
        # 2×10 + 3×5.50 = 20 + 16.50 = 36.50
        self.assertEqual(order.get_total_cost(), Decimal('36.50'))

    def test_discount_applied_to_total_cost(self):
        order = self._make_order(discount=10)
        self._make_item(order, price=Decimal('100.00'), quantity=1)
        # total = 100.00, discount = 10% → 10.00, net = 90.00
        self.assertEqual(order.get_total_cost(), Decimal('90.00'))

    def test_get_discount_zero_without_discount_field(self):
        order = self._make_order(discount=0)
        self._make_item(order, price=Decimal('100.00'), quantity=1)
        self.assertEqual(order.get_discount(), Decimal('0'))

    def test_get_discount_with_explicit_total(self):
        order = self._make_order(discount=20)
        # Provide the total cost directly
        self.assertEqual(order.get_discount(Decimal('50.00')), Decimal('10.00'))

    def test_get_discount_recomputes_total_when_none(self):
        order = self._make_order(discount=25)
        self._make_item(order, price=Decimal('40.00'), quantity=1)
        # total = 40, discount = 25% → 10.00
        self.assertEqual(order.get_discount(), Decimal('10.00'))


class OrderItemGetCostTests(OrderModelMixin):
    """``OrderItem.get_cost``."""

    def test_get_cost_single_item(self):
        order = self._make_order()
        item = self._make_item(order, price=Decimal('19.99'), quantity=1)
        self.assertEqual(item.get_cost(), Decimal('19.99'))

    def test_get_cost_multiplied_by_quantity(self):
        order = self._make_order()
        item = self._make_item(order, price=Decimal('10.00'), quantity=3)
        self.assertEqual(item.get_cost(), Decimal('30.00'))

    def test_get_cost_with_variant_price(self):
        """When a variant override exists, the stored item price is used."""
        order = self._make_order()
        item = self._make_item(order, price=Decimal('55.00'), quantity=2)
        self.assertEqual(item.get_cost(), Decimal('110.00'))


class OrderStripeUrlTests(OrderModelMixin):
    """``Order.get_stripe_url`` — test / live mode handling."""

    def test_no_stripe_id_returns_empty(self):
        order = self._make_order(stripe_id='')
        self.assertEqual(order.get_stripe_url(), '')

    @override_settings(STRIPE_SECRET_KEY='sk_test_123')
    def test_test_mode_url(self):
        order = self._make_order(stripe_id='pi_test_abc')
        url = order.get_stripe_url()
        self.assertIn('/test/', url)
        self.assertIn('pi_test_abc', url)
        self.assertTrue(url.startswith('https://dashboard.stripe.com'))

    @override_settings(STRIPE_SECRET_KEY='sk_live_456')
    def test_live_mode_url(self):
        order = self._make_order(stripe_id='pi_live_xyz')
        url = order.get_stripe_url()
        self.assertNotIn('/test/', url)
        self.assertIn('pi_live_xyz', url)
        self.assertTrue(url.startswith('https://dashboard.stripe.com'))

    @override_settings(STRIPE_SECRET_KEY='sk_live_456')
    def test_stripe_url_uses_dashboard_root(self):
        order = self._make_order(stripe_id='ch_123')
        url = order.get_stripe_url()
        self.assertTrue(url.startswith('https://dashboard.stripe.com/'))
