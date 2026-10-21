"""Unit tests for cart utility helpers (card 77ce5899).

Covers ``Cart`` methods that have no direct test coverage:
``get_total_price``, ``get_discount``, ``get_total_price_after_discount``,
``__len__``, ``clear``, ``save``, ``coupon`` property, and the
``_cart_key`` static helper.
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock

from django.conf import settings
from django.test import TestCase
from django.utils import timezone

from cart.utils import Cart
from coupons.models import Coupon
from shop.models import Category, Product, ProductVariant


class FakeSession(dict):
    """A dict that also supports attribute access (mimics Django session)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.modified = False


class CartModelFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(name='Test', slug='test')
        cls.product_a = Product.objects.create(
            category=cls.category,
            name='Product A',
            slug='product-a',
            price=Decimal('50.00'),
        )
        cls.product_b = Product.objects.create(
            category=cls.category,
            name='Product B',
            slug='product-b',
            price=Decimal('30.00'),
        )
        cls.variant = ProductVariant.objects.create(
            product=cls.product_a,
            name='Red / L',
            sku='A-RED-L',
            price=Decimal('55.00'),
        )

    def _make_cart(self):
        """Build a Cart backed by a fresh fake session."""
        request = MagicMock()
        request.session = FakeSession()
        return Cart(request)


class CartKeyTests(CartModelFixture):
    """``_cart_key`` produces the right session key shapes."""

    def test_plain_product_key_is_str_product_id(self):
        cart = self._make_cart()
        self.assertEqual(cart._cart_key(self.product_a), str(self.product_a.id))

    def test_variant_key_is_product_id_variant_id(self):
        cart = self._make_cart()
        key = cart._cart_key(self.product_a, self.variant)
        self.assertEqual(key, f'{self.product_a.id}:{self.variant.id}')

    def test_none_variant_uses_plain_key(self):
        cart = self._make_cart()
        self.assertEqual(cart._cart_key(self.product_a, None), str(self.product_a.id))


class CartTotalPriceTests(CartModelFixture):
    """``get_total_price`` sums price × quantity across all lines."""

    def test_empty_cart_total_is_zero(self):
        cart = self._make_cart()
        self.assertEqual(cart.get_total_price(), Decimal('0'))

    def test_single_plain_line(self):
        cart = self._make_cart()
        cart.add(self.product_a, quantity=2)
        self.assertEqual(cart.get_total_price(), Decimal('100.00'))

    def test_multiple_plain_lines(self):
        cart = self._make_cart()
        cart.add(self.product_a, quantity=1)
        cart.add(self.product_b, quantity=3)
        # 50.00 + 3×30.00 = 140.00
        self.assertEqual(cart.get_total_price(), Decimal('140.00'))

    def test_variant_line_uses_variant_price(self):
        cart = self._make_cart()
        cart.add(self.product_a, quantity=1, variant=self.variant)
        # Variant price is 55.00
        self.assertEqual(cart.get_total_price(), Decimal('55.00'))

    def test_mixed_plain_and_variant_lines(self):
        cart = self._make_cart()
        cart.add(self.product_a, quantity=1)                     # 50.00
        cart.add(self.product_a, quantity=1, variant=self.variant)  # 55.00
        self.assertEqual(cart.get_total_price(), Decimal('105.00'))


class CartDiscountTests(CartModelFixture):
    """``get_discount`` and ``get_total_price_after_discount``."""

    def test_discount_zero_without_coupon(self):
        cart = self._make_cart()
        cart.add(self.product_a, quantity=1)
        self.assertEqual(cart.get_discount(), Decimal('0'))

    def test_discount_zero_with_cleared_coupon(self):
        request = MagicMock()
        request.session = FakeSession({'coupon_id': None})
        cart = Cart(request)
        cart.add(self.product_a, quantity=1)
        self.assertEqual(cart.get_discount(), Decimal('0'))

    def test_discount_with_valid_coupon(self):
        now = timezone.now()
        coupon = Coupon.objects.create(
            code='SAVE20',
            valid_from=now,
            valid_to=now + timedelta(days=1),
            discount=20,
            active=True,
        )
        request = MagicMock()
        request.session = FakeSession({'coupon_id': coupon.id})
        cart = Cart(request)
        cart.add(self.product_a, quantity=2)
        # total = 100.00, discount = 20% → 20.00
        self.assertEqual(cart.get_discount(), Decimal('20.00'))
        self.assertEqual(cart.get_total_price_after_discount(), Decimal('80.00'))

    def test_discount_with_coupon_that_points_to_missing_row(self):
        """A coupon_id in the session that no longer exists → no discount."""
        request = MagicMock()
        request.session = FakeSession({'coupon_id': 999999})
        cart = Cart(request)
        cart.add(self.product_a, quantity=1)
        self.assertEqual(cart.get_discount(), Decimal('0'))
        self.assertEqual(cart.get_total_price_after_discount(), Decimal('50.00'))


class CartLenAndClearTests(CartModelFixture):
    """``__len__``, ``clear``, and ``save``."""

    def test_len_empty_is_zero(self):
        cart = self._make_cart()
        self.assertEqual(len(cart), 0)

    def test_len_singles_line(self):
        cart = self._make_cart()
        cart.add(self.product_a, quantity=3)
        self.assertEqual(len(cart), 3)

    def test_len_sums_across_lines(self):
        cart = self._make_cart()
        cart.add(self.product_a, quantity=2)
        cart.add(self.product_b, quantity=1)
        self.assertEqual(len(cart), 3)

    def test_clear_removes_session_key(self):
        cart = self._make_cart()
        cart.add(self.product_a, quantity=1)
        cart.clear()
        self.assertNotIn(settings.CART_SESSION_ID, cart.session)

    def test_save_marks_session_modified(self):
        cart = self._make_cart()
        cart.add(self.product_a, quantity=1)
        self.assertTrue(cart.session.modified)
