from io import StringIO

from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.test import TestCase

from orders.models import Order
from shop.models import Category, Product
from decimal import Decimal

User = get_user_model()


class SeedCommandTests(TestCase):
    def test_seed_creates_demo_users_and_orders_idempotent(self):
        cat = Category.objects.create(name='Cat', slug='cat')
        Product.objects.create(
            category=cat,
            name='P1',
            slug='p1',
            price=Decimal('10.00'),
        )
        Product.objects.create(
            category=cat,
            name='P2',
            slug='p2',
            price=Decimal('20.00'),
        )
        silent = StringIO()
        call_command(
            'seed',
            '--no-images',
            '--no-superuser',
            verbosity=0,
            stdout=silent,
            stderr=silent,
        )
        self.assertTrue(User.objects.filter(username='demo').exists())
        self.assertTrue(User.objects.filter(username='alice').exists())
        demo = User.objects.get(username='demo')
        self.assertGreaterEqual(Order.objects.filter(user=demo).count(), 2)
        n_orders = Order.objects.filter(user=demo).count()
        call_command(
            'seed',
            '--no-images',
            '--no-superuser',
            verbosity=0,
            stdout=silent,
            stderr=silent,
        )
        self.assertEqual(Order.objects.filter(user=demo).count(), n_orders)
