"""Tests for the return / refund request workflow (card 4fcgcg9e).

Covers:
* Customer-facing form: permissions, paid-only access, ownership, rate limit.
* Model behaviour: is_full_order, get_item_cost, status flow.
* Admin actions: approve, reject, mark_refunded (with idempotency + Stripe
  error surfacing), mark_canceled.
* refund_order integration: the admin's mark_refunded delegates to the existing
  idempotent refund_order primitive; Stripe errors are caught and surfaced.
"""
from decimal import Decimal
from unittest.mock import patch

import stripe
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from orders.models import Order, OrderItem
from returns.models import ReturnRequest
from shop.models import Category, Product

from payment.refunds import REASON_OK


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


def _order(paid=True, stripe_id='pi_test_123', price='100.00', quantity=1, user=None):
    order = Order.objects.create(
        first_name='Test',
        last_name='User',
        email='t@example.com',
        address='123 St',
        postal_code='12345',
        city='City',
        paid=paid,
        stripe_id=stripe_id or '',
        user=user,
    )
    OrderItem.objects.create(
        order=order,
        product=_product(price),
        price=Decimal(price),
        quantity=quantity,
    )
    return order


class ReturnRequestModelTests(TestCase):
    """Model-level behaviour: status flow, cost helpers, ownership."""

    def test_default_status_is_submitted(self):
        order = _order()
        rr = ReturnRequest.objects.create(
            order=order,
            reason='Damaged',
            user=order.user,
        )
        self.assertEqual(rr.status, ReturnRequest.Status.SUBMITTED)

    def test_is_full_order_true_for_null_item(self):
        rr = ReturnRequest.objects.create(
            order=_order(),
            reason='Other',
        )
        self.assertTrue(rr.is_full_order())

    def test_is_full_order_false_for_item_scoped(self):
        order = _order()
        item = order.items.first()
        rr = ReturnRequest.objects.create(
            order=order,
            item=item,
            reason='Other',
        )
        self.assertFalse(rr.is_full_order())

    def test_get_item_cost_returns_order_total_for_full_order(self):
        order = _order(price='100.00', quantity=1)
        rr = ReturnRequest.objects.create(order=order, reason='Other')
        self.assertEqual(rr.get_item_cost(), Decimal('100.00'))

    def test_get_item_cost_returns_item_cost_for_item_scoped(self):
        order = _order(price='100.00', quantity=2)
        item = order.items.first()
        rr = ReturnRequest.objects.create(order=order, item=item, reason='Other')
        self.assertEqual(rr.get_item_cost(), Decimal('200.00'))

    def test_str_contains_order_id(self):
        order = _order()
        rr = ReturnRequest.objects.create(order=order, reason='Other')
        self.assertIn(str(order.id), str(rr))
