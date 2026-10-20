"""Inventory tracking (card 44ge8hgb).

Nullable ``stock`` (``NULL = untracked`` = always sellable) on Product and
ProductVariant, decremented once per paid order inside the webhook's guarded
``updated == 1`` branch, clamped at 0, never negative.
"""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from orders.models import Order, OrderItem
from shop.inventory import decrement_for_order
from shop.models import Category, Product, ProductVariant

from payment.tests.test_stripe_webhook import _checkout_completed_event

User = get_user_model()


def _create_order(**overrides):
    fields = {
        'first_name': 'Ada',
        'last_name': 'Lovelace',
        'email': 'ada@example.com',
        'address': '1 Analytical Engine Rd',
        'postal_code': 'OX1',
        'city': 'Oxford',
        'paid': False,
    }
    fields.update(overrides)
    return Order.objects.create(**fields)


class InventoryModelTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Home', slug='home')

    def test_untracked_product_is_always_in_stock(self):
        product = Product.objects.create(
            category=self.category, name='Mug', slug='mug',
            price=Decimal('10.00'), stock=None,
        )
        self.assertTrue(product.is_in_stock())

    def test_tracked_product_in_stock_when_positive(self):
        product = Product.objects.create(
            category=self.category, name='Mug', slug='mug',
            price=Decimal('10.00'), stock=3,
        )
        self.assertTrue(product.is_in_stock())

    def test_tracked_product_out_of_stock_when_zero(self):
        product = Product.objects.create(
            category=self.category, name='Mug', slug='mug',
            price=Decimal('10.00'), stock=0,
        )
        self.assertFalse(product.is_in_stock())

    def test_untracked_variant_is_always_in_stock(self):
        product = Product.objects.create(
            category=self.category, name='Mug', slug='mug',
            price=Decimal('10.00'),
        )
        variant = ProductVariant.objects.create(
            product=product, name='Red', stock=None,
        )
        self.assertTrue(variant.is_in_stock())

    def test_tracked_variant_in_stock_when_positive(self):
        product = Product.objects.create(
            category=self.category, name='Mug', slug='mug',
            price=Decimal('10.00'),
        )
        variant = ProductVariant.objects.create(
            product=product, name='Red', stock=3,
        )
        self.assertTrue(variant.is_in_stock())

    def test_tracked_variant_out_of_stock_when_zero(self):
        product = Product.objects.create(
            category=self.category, name='Mug', slug='mug',
            price=Decimal('10.00'),
        )
        variant = ProductVariant.objects.create(
            product=product, name='Red', stock=0,
        )
        self.assertFalse(variant.is_in_stock())


class InventoryDecrementTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Home', slug='home')
        self.product = Product.objects.create(
            category=self.category, name='Mug', slug='mug',
            price=Decimal('10.00'), stock=10,
        )
        self.variant = ProductVariant.objects.create(
            product=self.product, name='Red', sku='MUG-RED',
            price=Decimal('12.00'), stock=4,
        )

    def test_decrement_product_line_and_variant_line_in_one_order(self):
        order = _create_order()
        OrderItem.objects.create(
            order=order, product=self.product, quantity=3, price=self.product.price,
        )
        OrderItem.objects.create(
            order=order, product=self.product, variant=self.variant, quantity=2,
            price=self.variant.unit_price(),
        )
        decrement_for_order(order)

        self.product.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertEqual(self.product.stock, 7)   # 10 - 3
        self.assertEqual(self.variant.stock, 2)   # 4 - 2

    def test_untracked_target_is_skipped_no_op(self):
        untracked_product = Product.objects.create(
            category=self.category, name='Plain', slug='plain',
            price=Decimal('5.00'), stock=None,
        )
        untracked_variant = ProductVariant.objects.create(
            product=self.product, name='Blue', stock=None,
        )
        order = _create_order()
        OrderItem.objects.create(
            order=order, product=untracked_product, quantity=5,
            price=untracked_product.price,
        )
        OrderItem.objects.create(
            order=order, product=self.product, variant=untracked_variant, quantity=5,
            price=untracked_variant.unit_price(),
        )
        # must not raise and must not touch untracked rows
        decrement_for_order(order)

        untracked_product.refresh_from_db()
        untracked_variant.refresh_from_db()
        self.assertIsNone(untracked_product.stock)
        self.assertIsNone(untracked_variant.stock)

    def test_clamp_never_negative_logs_warning(self):
        self.product.stock = 2
        self.product.save()
        order = _create_order()
        OrderItem.objects.create(
            order=order, product=self.product, quantity=5, price=self.product.price,
        )

        with self.assertLogs('shop.inventory', level='WARNING') as cm:
            decrement_for_order(order)

        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 0)
        self.assertIn('clamped to 0', ''.join(cm.output))


@override_settings(STRIPE_WEBHOOK_SECRET='whsec_test_secret')
@patch('payment.views.stripe_webhook_view.payment_completed.delay')
@patch('shop.inventory.decrement_for_order')
class WebhookInventoryTests(TestCase):
    """The webhook must decrement exactly once per paid order; replays are
    no-ops (idempotency gate) and a fraud flag never blocks stock."""

    def setUp(self):
        self.client = Client()
        self.category = Category.objects.create(name='Home', slug='home')
        self.product = Product.objects.create(
            category=self.category, name='Mug', slug='mug',
            price=Decimal('10.00'), stock=10,
        )
        self.order = _create_order()
        OrderItem.objects.create(
            order=self.order, product=self.product, quantity=2,
            price=self.product.price,
        )
        self.url = '/payment/webhook/'

    def _deliver(self, event):
        with patch('stripe.Webhook.construct_event', return_value=event):
            return self.client.post(
                self.url,
                b'{"type": "ignored"}',
                content_type='application/json',
                HTTP_STRIPE_SIGNATURE='v1,test',
            )

    def test_paid_event_decrements_exactly_once(self, mock_decrement, mock_task):
        event = _checkout_completed_event(self.order.id)
        response = self._deliver(event)

        self.assertEqual(response.status_code, 200)
        mock_decrement.assert_called_once_with(self.order)
        mock_task.assert_called_once_with(self.order.id)
        self.order.refresh_from_db()
        self.assertTrue(self.order.paid)

    def test_replay_does_not_decrement_again(self, mock_decrement, mock_task):
        event = _checkout_completed_event(self.order.id)
        self._deliver(event)
        self._deliver(event)  # Stripe at-least-once replay

        self.assertEqual(mock_decrement.call_count, 1)
        self.assertEqual(mock_task.call_count, 1)
        self.order.refresh_from_db()
        self.assertTrue(self.order.paid)

    def test_flagged_order_still_decrements(self, mock_decrement, mock_task):
        self.order.is_flagged = True
        self.order.fraud_notes = 'velocity_email'
        self.order.save()

        event = _checkout_completed_event(self.order.id)
        response = self._deliver(event)

        self.assertEqual(response.status_code, 200)
        mock_decrement.assert_called_once_with(self.order)
        mock_task.assert_called_once_with(self.order.id)


class StockBadgeTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Home', slug='home')

    def test_detail_shows_in_stock_for_untracked_without_numeric_count(self):
        product = Product.objects.create(
            category=self.category, name='Mug', slug='mug',
            price=Decimal('10.00'), stock=None,
        )
        html = self.client.get(product.get_absolute_url()).content.decode()
        self.assertIn('In stock', html)
        # untracked shows a status chip, never a numeric count
        self.assertNotRegex(html, r'In stock \d')

    def test_detail_shows_in_stock_for_tracked_positive(self):
        product = Product.objects.create(
            category=self.category, name='Mug', slug='mug',
            price=Decimal('10.00'), stock=3,
        )
        html = self.client.get(product.get_absolute_url()).content.decode()
        self.assertIn('In stock', html)
        self.assertNotIn('Sold out', html)

    def test_detail_shows_sold_out_when_tracked_zero(self):
        product = Product.objects.create(
            category=self.category, name='Mug', slug='mug',
            price=Decimal('10.00'), stock=0,
        )
        html = self.client.get(product.get_absolute_url()).content.decode()
        self.assertIn('Sold out', html)
        self.assertNotIn('In stock', html)


class InventoryAdminTests(TestCase):
    def test_product_admin_shows_stock_and_is_in_stock(self):
        Product.objects.create(
            category=self.category, name='Mug', slug='mug',
            price=Decimal('10.00'), stock=5,
        )
        staff = User.objects.create_superuser('inv-admin', 'inv@example.com', 'password')
        self.client.force_login(staff)

        response = self.client.get(reverse('admin:shop_product_changelist'))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('Stock', html)
        self.assertIn('In stock', html)

    def setUp(self):
        self.category = Category.objects.create(name='Home', slug='home')

    def test_variant_inline_shows_stock(self):
        product = Product.objects.create(
            category=self.category, name='Mug', slug='mug',
            price=Decimal('10.00'),
        )
        ProductVariant.objects.create(
            product=product, name='Red', sku='MUG-RED', stock=4,
        )
        staff = User.objects.create_superuser('inv-admin-2', 'inv2@example.com', 'password')
        self.client.force_login(staff)

        response = self.client.get(reverse('admin:shop_product_change', args=[product.id]))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('stock', html)
