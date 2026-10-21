"""Unit tests for cart AJAX helper functions (card 77ce5899).

These exercise ``format_form_errors``, ``cart_summary_json``,
``cart_error_json``, and ``as_price`` as pure functions, complementing
the integration-level AJAX tests in ``test_ajax.py``.
"""
import json
from decimal import Decimal
from unittest.mock import MagicMock

from django.http import JsonResponse
from django.test import TestCase

from cart.utils import Cart
from cart.utils.ajax import (
    as_price,
    cart_error_json,
    cart_summary_json,
    format_form_errors,
)
from cart.forms import CartAddProductForm
from shop.models import Category, Product


class FakeSession(dict):
    """A dict that also supports attribute access (mimics Django session)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.modified = False


class AsPriceTests(TestCase):
    """``as_price`` formats a numeric/Decimal as a 2-dp string."""

    def test_integer_input(self):
        self.assertEqual(as_price(5), '5.00')

    def test_decimal_input(self):
        self.assertEqual(as_price(Decimal('19.99')), '19.99')

    def test_float_string_input(self):
        self.assertEqual(as_price('42'), '42.00')

    def test_truncates_to_two_decimals(self):
        self.assertEqual(as_price(Decimal('3.14159')), '3.14')

    def test_rounding_to_two_decimals(self):
        self.assertEqual(as_price(Decimal('2.999')), '3.00')

    def test_zero(self):
        self.assertEqual(as_price(0), '0.00')


class FormatFormErrorsTests(TestCase):
    """``format_form_errors`` flattens a Django form's errors to a dict."""

    def test_valid_form_returns_empty_dict(self):
        form = CartAddProductForm(data={'quantity': '2'})
        self.assertTrue(form.is_valid())
        result = format_form_errors(form)
        self.assertEqual(result, {})

    def test_invalid_quantity_produces_error_list(self):
        form = CartAddProductForm(data={'quantity': '0'})
        self.assertFalse(form.is_valid())
        result = format_form_errors(form)
        self.assertIn('quantity', result)
        self.assertIsInstance(result['quantity'], list)
        self.assertEqual(len(result['quantity']), 1)

    def test_missing_required_field(self):
        form = CartAddProductForm(data={})
        self.assertFalse(form.is_valid())
        result = format_form_errors(form)
        self.assertIn('quantity', result)
        self.assertIsInstance(result['quantity'][0], str)


class CartErrorJsonTests(TestCase):
    """``cart_error_json`` builds a JSON error response."""

    def test_basic_error_response(self):
        resp = cart_error_json('Something went wrong')
        self.assertIsInstance(resp, JsonResponse)
        self.assertEqual(resp.status_code, 400)
        body = json.loads(resp.content)
        self.assertFalse(body['ok'])
        self.assertEqual(body['error'], 'Something went wrong')
        self.assertNotIn('errors', body)

    def test_error_response_with_custom_status(self):
        resp = cart_error_json('Not found', status=404)
        self.assertEqual(resp.status_code, 404)
        body = json.loads(resp.content)
        self.assertEqual(body['error'], 'Not found')

    def test_error_response_with_form_errors(self):
        resp = cart_error_json(
            'Invalid quantity',
            status=400,
            errors={'quantity': ['Ensure this value is greater than or equal to 1.']},
        )
        body = json.loads(resp.content)
        self.assertFalse(body['ok'])
        self.assertEqual(body['error'], 'Invalid quantity')
        self.assertIn('errors', body)
        self.assertEqual(body['errors']['quantity'][0], 'Ensure this value is greater than or equal to 1.')


class CartSummaryJsonTests(TestCase):
    """``cart_summary_json`` builds the success JSON body for an AJAX cart."""

    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(name='Home', slug='home')
        cls.product = Product.objects.create(
            category=cls.category,
            name='Mug',
            slug='mug',
            price=Decimal('10.00'),
        )

    def _make_cart(self):
        """Build a Cart backed by a fresh fake session."""
        request = MagicMock()
        request.session = FakeSession()
        return Cart(request)

    def test_empty_cart_summary(self):
        cart = self._make_cart()
        body = cart_summary_json(cart)
        self.assertTrue(body['ok'])
        self.assertEqual(body['total'], '0.00')
        self.assertEqual(body['total_items'], 0)
        self.assertEqual(body['subtotal'], '0.00')
        self.assertEqual(body['discount'], '0.00')

    def test_single_item_summary(self):
        cart = self._make_cart()
        cart.add(self.product, quantity=2)
        body = cart_summary_json(cart)
        self.assertTrue(body['ok'])
        self.assertEqual(body['total'], '20.00')
        self.assertEqual(body['total_items'], 2)
        self.assertEqual(body['subtotal'], '20.00')
        self.assertEqual(body['discount'], '0.00')

    def test_summary_with_discount(self):
        from datetime import timedelta
        from django.utils import timezone
        from coupons.models import Coupon

        now = timezone.now()
        coupon = Coupon.objects.create(
            code='SAVE50',
            valid_from=now,
            valid_to=now + timedelta(days=1),
            discount=50,
            active=True,
        )
        request = MagicMock()
        request.session = FakeSession({'coupon_id': coupon.id})
        cart = Cart(request)
        cart.add(self.product, quantity=2)

        body = cart_summary_json(cart)
        self.assertEqual(body['total'], '10.00')      # 50% off 20.00
        self.assertEqual(body['subtotal'], '20.00')
        self.assertEqual(body['discount'], '10.00')
