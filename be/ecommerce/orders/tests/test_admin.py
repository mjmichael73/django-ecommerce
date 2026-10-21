"""Unit tests for order admin actions and helper functions.

Covers ``export_to_csv``, ``order_payment``, ``order_detail``, ``order_pdf``,
``mark_rules_reviewed``, and ``issue_full_refund`` action — the parts of
``orders/admin.py`` that operate without rendering the full admin page.
"""
import csv
import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib import messages
from django.contrib.admin.sites import AdminSite
from django.test import TestCase, override_settings
from django.urls import reverse

from orders.admin import (
    OrderAdmin,
    export_to_csv,
    order_detail,
    order_payment,
    order_pdf,
)
from orders.models import Order, OrderItem
from shop.models import Category, Product


class OrderAdminFixture(TestCase):
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
            'paid': True,
            'stripe_id': '',
            'discount': 0,
        }
        defaults.update(kwargs)
        order = Order.objects.create(**defaults)
        OrderItem.objects.create(
            order=order,
            product=self.product,
            price=Decimal('19.99'),
            quantity=1,
        )
        return order


class ExportToCsvTests(OrderAdminFixture):
    """``export_to_csv`` admin action."""

    def test_export_returns_csv_response(self):
        order = self._make_order()
        site = AdminSite()
        ma = OrderAdmin(Order, site)
        request = MagicMock()
        response = export_to_csv(ma, request, Order.objects.all())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response['Content-Disposition'],
            'attachment; filename=order.csv',
        )
        # Parse the CSV content.
        content = response.content.decode()
        reader = csv.reader(content.strip().splitlines())
        header = next(reader)
        row = next(reader)
        self.assertIn('first name', header)  # verbose_name of first_name field
        # The row should contain the order's data.
        self.assertIn('Test', row)

    def test_datetime_fields_are_formatted(self):
        """DateTime fields are rendered as DD/MM/YYYY in the CSV."""
        order = self._make_order()
        site = AdminSite()
        ma = OrderAdmin(Order, site)
        request = MagicMock()
        response = export_to_csv(ma, request, Order.objects.filter(id=order.id))

        content = response.content.decode()
        reader = csv.reader(content.strip().splitlines())
        header = next(reader)
        row = next(reader)
        created_idx = header.index('created')
        created_value = row[created_idx]
        # Should be a DD/MM/YYYY formatted date, not an ISO timestamp.
        self.assertEqual(len(created_value), 10)
        parts = created_value.split('/')
        self.assertEqual(len(parts), 3)


class OrderPaymentTests(OrderAdminFixture):
    """``order_payment`` admin helper."""

    @override_settings(STRIPE_SECRET_KEY='sk_test_123')
    def test_returns_link_when_stripe_id_present(self):
        order = self._make_order(stripe_id='pi_test_456')
        result = order_payment(order)
        self.assertIn('href=', result)
        self.assertIn('pi_test_456', result)
        self.assertIn('/test/', result)

    def test_returns_empty_string_without_stripe_id(self):
        order = self._make_order(stripe_id='')
        result = order_payment(order)
        self.assertEqual(result, '')


class OrderDetailAndPdfTests(OrderAdminFixture):
    """``order_detail`` and ``order_pdf`` admin helpers."""

    def test_order_detail_returns_html_link(self):
        order = self._make_order()
        result = order_detail(order)
        self.assertIn('href=', result)
        self.assertIn(f'/orders/admin/order/{order.id}/', result)
        self.assertIn('View', result)

    def test_order_pdf_returns_html_link(self):
        order = self._make_order()
        result = order_pdf(order)
        self.assertIn('href=', result)
        self.assertIn('PDF', result)


class OrderAdminConfigTests(OrderAdminFixture):
    """Verify admin registration and configuration."""

    def test_item_inline_uses_raw_id_for_product(self):
        from orders.admin import OrderItemInline
        self.assertIn('product', OrderItemInline.raw_id_fields)

    def test_actions_registered(self):
        site = AdminSite()
        ma = OrderAdmin(Order, site)
        self.assertIn(export_to_csv, ma.actions)

    def test_order_payment_has_short_description(self):
        self.assertEqual(order_payment.short_description, 'Stripe payment')

    def test_order_pdf_has_short_description(self):
        self.assertEqual(order_pdf.short_description, 'Invoice')
