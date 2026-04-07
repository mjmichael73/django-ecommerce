from decimal import Decimal
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse

from orders.models import Order, OrderItem
from shop.models import Category, Product


@patch('orders.views.order_create_view.order_created.delay')
class CheckoutCreateOrderTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.category = Category.objects.create(name='Books', slug='books')
        self.product = Product.objects.create(
            category=self.category,
            name='Guide',
            slug='guide',
            price=Decimal('19.99'),
        )
        session = self.client.session
        session['cart'] = {
            str(self.product.id): {'quantity': 2, 'price': str(self.product.price)},
        }
        session.save()

    def test_post_creates_order_items_and_clears_cart(self, _mock_delay):
        url = reverse('orders:order_create')
        payload = {
            'first_name': 'Ada',
            'last_name': 'Lovelace',
            'email': 'ada@example.com',
            'address': '1 Analytical Engine Rd',
            'postal_code': 'OX1',
            'city': 'Oxford',
        }
        response = self.client.post(url, payload)

        self.assertEqual(Order.objects.count(), 1)
        order = Order.objects.get()
        self.assertIsNone(order.user)
        self.assertEqual(order.first_name, 'Ada')
        self.assertFalse(order.paid)
        self.assertEqual(OrderItem.objects.filter(order=order).count(), 1)
        item = OrderItem.objects.get(order=order)
        self.assertEqual(item.product_id, self.product.id)
        self.assertEqual(item.quantity, 2)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('payment:process'))
        self.assertNotIn('cart', self.client.session)

    def test_get_renders_form(self, _mock_delay):
        url = reverse('orders:order_create')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
