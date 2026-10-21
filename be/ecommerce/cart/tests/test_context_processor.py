"""Tests for the cart context processor."""
from decimal import Decimal
from unittest.mock import MagicMock

from django.test import RequestFactory, TestCase

from cart.context_processors import cart as cart_context_processor
from shop.models import Category, Product


class CartContextProcessorTests(TestCase):
    def test_returns_cart_in_context(self):
        """The context processor returns a dict with a 'cart' key."""
        request = RequestFactory().get('/')
        request.session = {}
        result = cart_context_processor(request)
        self.assertIn('cart', result)
        from cart.utils import Cart
        self.assertIsInstance(result['cart'], Cart)

    def test_cart_is_empty_for_new_session(self):
        """An empty session produces an empty cart (0 items)."""
        request = RequestFactory().get('/')
        request.session = {}
        result = cart_context_processor(request)
        self.assertEqual(len(result['cart']), 0)
