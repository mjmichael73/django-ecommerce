"""Unit tests for Celery order tasks (orders/tasks/).

The tasks ``order_created`` and ``payment_completed`` are not directly tested
elsewhere — these cover their happy paths, DB-lookup behaviour, and email
construction.
"""
from io import BytesIO
from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.test import TestCase, override_settings

from orders.models import Order, OrderItem
from orders.tasks import order_created, payment_completed
from shop.models import Category, Product


class OrderTaskFixture(TestCase):
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
            'first_name': 'Ada',
            'last_name': 'Lovelace',
            'email': 'ada@example.com',
            'address': '1 Analytical Engine Rd',
            'postal_code': 'OX1',
            'city': 'Oxford',
            'paid': True,
            'stripe_id': 'pi_test_123',
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


class OrderCreatedTaskTests(OrderTaskFixture):
    """``order_created`` sends a notification email."""

    @patch('orders.tasks.order_created_task.send_mail')
    def test_task_sends_email_to_order_email(self, mock_send):
        mock_send.return_value = 1
        order = self._make_order()

        result = order_created(order.id)

        self.assertEqual(result, 1)
        mock_send.assert_called_once()
        args = mock_send.call_args
        self.assertEqual(args[0][0], f'Order nr. {order.id}')
        # The email goes to the order's email address
        self.assertIn(order.email, args[0][3])

    @patch('orders.tasks.order_created_task.send_mail')
    def test_subject_contains_order_id(self, mock_send):
        mock_send.return_value = 1
        order = self._make_order()

        order_created(order.id)

        called_args = mock_send.call_args[0]
        self.assertIn(str(order.id), called_args[0])

    @patch('orders.tasks.order_created_task.send_mail')
    def test_nonexistent_order_raises(self, mock_send):
        """A missing order id raises DoesNotExist (task failure is expected)."""
        from django.core.exceptions import ObjectDoesNotExist
        with self.assertRaises(ObjectDoesNotExist):
            order_created(999999)
        mock_send.assert_not_called()


class PaymentCompletedTaskTests(OrderTaskFixture):
    """``payment_completed`` sends a notification email after payment."""

    @patch('orders.tasks.payment_completed_task.send_mail')
    def test_task_sends_email_to_order_email(self, mock_send):
        mock_send.return_value = 1
        order = self._make_order()

        result = payment_completed(order.id)

        self.assertEqual(result, 1)
        mock_send.assert_called_once()
        args = mock_send.call_args
        self.assertEqual(args[0][0], f'Order nr. {order.id}')
        # The email goes to the order's email address
        self.assertIn(order.email, args[0][3])

    @patch('orders.tasks.payment_completed_task.send_mail')
    def test_task_subject_contains_order_id(self, mock_send):
        mock_send.return_value = 1
        order = self._make_order()

        payment_completed(order.id)

        called_args = mock_send.call_args[0]
        self.assertIn(str(order.id), called_args[0])
