"""Unit tests for Order.__str__ and OrderItem.__str__ (orders/models/)."""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from orders.models import Order, OrderItem
from shop.models import Category, Product


class OrderStrTests(TestCase):
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

    def test_order_str_includes_id(self):
        order = self._make_order()
        self.assertEqual(str(order), f'Order {order.id}')

    def test_orderitem_str_returns_id(self):
        order = self._make_order()
        item = OrderItem.objects.create(
            order=order,
            product=self.product,
            price=Decimal('19.99'),
            quantity=1,
        )
        self.assertEqual(str(item), str(item.id))


class OrderCreateVariantTests(TestCase):
    """Order create view variant handling (orders/views/order_create_view.py).

    Specifically tests that:
    - A variant object attached by Cart.__iter__ is passed to OrderItem (not None)
    - When a variant row has been deleted (variant is None in iteration),
      variant=None is stored (SET_NULL semantics) without raising
    - Authenticated users are associated with their order
    """

    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(name='Books', slug='books')
        cls.product = Product.objects.create(
            category=cls.category,
            name='Guide',
            slug='guide',
            price=Decimal('19.99'),
        )
        from shop.models import ProductVariant
        cls.variant = ProductVariant.objects.create(
            product=cls.product,
            name='Red / L',
            sku='GUIDE-RED-L',
            price=Decimal('25.00'),
        )

    def setUp(self):
        from unittest.mock import patch
        self.patcher = patch('orders.views.order_create_view.order_created.delay')
        self.mock_delay = self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.url = reverse('orders:order_create')

    def test_authenticated_checkout_sets_user(self):
        """Authenticated users get their order associated with their account."""
        from django.test import Client
        from django.contrib.auth import get_user_model
        User = get_user_model()

        client = Client()
        user = User.objects.create_user('buyer', 'buyer@example.com', 'complex-pass')
        client.force_login(user)

        # Add product to cart via session
        session = client.session
        session['cart'] = {
            str(self.product.id): {'quantity': 1, 'price': str(self.product.price)},
        }
        session.save()

        payload = {
            'first_name': 'Sam',
            'last_name': 'Spade',
            'email': 'buyer@example.com',
            'address': '123 St',
            'postal_code': '12345',
            'city': 'City',
        }
        response = client.post(self.url, payload)

        self.assertEqual(response.status_code, 302)
        order = Order.objects.get()
        self.assertEqual(order.user_id, user.pk)

    def test_variant_stored_when_present_in_cart(self):
        """When a variant line is in the cart, the OrderItem stores it."""
        from django.test import Client

        client = Client()
        session = client.session
        session['cart'] = {
            f'{self.product.id}:{self.variant.id}': {
                'quantity': 1,
                'price': str(self.variant.unit_price()),
                'variant_id': self.variant.id,
                'variant_name': self.variant.name,
            },
        }
        session.save()

        payload = {
            'first_name': 'Sam',
            'last_name': 'Spade',
            'email': 'buyer@example.com',
            'address': '123 St',
            'postal_code': '12345',
            'city': 'City',
        }
        response = client.post(self.url, payload)
        self.assertEqual(response.status_code, 302)

        order = Order.objects.get()
        item = OrderItem.objects.get(order=order)
        self.assertEqual(item.variant_id, self.variant.id)
        self.assertEqual(item.price, Decimal('25.00'))

    def test_deleted_variant_stores_none_without_raising(self):
        """When the variant row is deleted after the cart line was created,
        iteration attaches variant=None and the OrderItem stores NULL."""
        from django.test import Client

        client = Client()
        # Add variant line to cart, then delete the variant row.
        session = client.session
        session['cart'] = {
            f'{self.product.id}:{self.variant.id}': {
                'quantity': 1,
                'price': str(self.variant.unit_price()),
                'variant_id': self.variant.id,
                'variant_name': self.variant.name,
            },
        }
        session.save()

        # Delete the variant — simulating it being removed from the DB.
        variant_id = self.variant.id
        self.variant.delete()

        payload = {
            'first_name': 'Sam',
            'last_name': 'Spade',
            'email': 'buyer@example.com',
            'address': '123 St',
            'postal_code': '12345',
            'city': 'City',
        }
        # This must not raise — the view reads item['variant'] which is None
        # after __iter__ resolves the deleted row.
        response = client.post(self.url, payload)
        self.assertEqual(response.status_code, 302)

        order = Order.objects.get()
        item = OrderItem.objects.get(order=order)
        # variant FK is nullable -> stored as None (not a dead FK).
        self.assertIsNone(item.variant)
