"""Unit tests for shop model methods.

Covers ``Product.is_in_stock``, ``get_absolute_url``, ``average_rating``,
``review_count``, and ``Category.get_absolute_url`` / ``__str__`` — the
model-level helpers that render logic relies on but have no dedicated tests.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from shop.models import Category, Product, ProductVariant, ProductReview


User = get_user_model()


class CategoryModelTests(TestCase):
    """``Category`` model methods."""

    def test_str_returns_name(self):
        cat = Category.objects.create(name='Electronics', slug='electronics')
        self.assertEqual(str(cat), 'Electronics')

    def test_get_absolute_url(self):
        cat = Category.objects.create(name='Electronics', slug='electronics')
        url = cat.get_absolute_url()
        self.assertEqual(url, reverse('shop:product_list_by_category', args=['electronics']))


class ProductModelTests(TestCase):
    """``Product`` model methods."""

    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(name='Test', slug='test')
        cls.product = Product.objects.create(
            category=cls.category,
            name='Widget',
            slug='widget',
            price=Decimal('25.00'),
        )

    def test_str_returns_name(self):
        self.assertEqual(str(self.product), 'Widget')

    def test_get_absolute_url(self):
        url = self.product.get_absolute_url()
        self.assertEqual(url, reverse('shop:product_detail', args=[self.product.id, 'widget']))

    def test_untracked_stock_is_in_stock(self):
        """NULL stock means untracked → always sellable."""
        p = Product.objects.create(
            category=self.category, name='Unpriced', slug='unpriced',
            price=Decimal('10.00'), stock=None,
        )
        self.assertTrue(p.is_in_stock())

    def test_zero_tracked_stock_is_out_of_stock(self):
        p = Product.objects.create(
            category=self.category, name='Sold Out', slug='sold-out',
            price=Decimal('10.00'), stock=0,
        )
        self.assertFalse(p.is_in_stock())

    def test_positive_tracked_stock_is_in_stock(self):
        p = Product.objects.create(
            category=self.category, name='In Stock', slug='in-stock',
            price=Decimal('10.00'), stock=5,
        )
        self.assertTrue(p.is_in_stock())

    def test_average_rating_none_without_reviews(self):
        self.assertIsNone(self.product.average_rating())

    def test_average_rating_with_approved_reviews(self):
        user = User.objects.create_user('u1', 'u1@e.com', 'pass-phrase-99')
        ProductReview.objects.create(
            product=self.product, user=user, rating=5, title='T', body='B',
        )
        ProductReview.objects.create(
            product=self.product, user=User.objects.create_user('u2', 'u2@e.com', 'pass-phrase-99'),
            rating=3, title='T', body='B',
        )
        # (5 + 3) / 2 = 4.0
        self.assertEqual(self.product.average_rating(), Decimal('4.0'))

    def test_average_rating_excludes_unapproved(self):
        user = User.objects.create_user('u1', 'u1@e.com', 'pass-phrase-99')
        ProductReview.objects.create(
            product=self.product, user=user, rating=5, title='T', body='B', approved=False,
        )
        self.assertIsNone(self.product.average_rating())

    def test_review_count_zero_without_reviews(self):
        self.assertEqual(self.product.review_count(), 0)

    def test_review_count_counts_approved_only(self):
        user = User.objects.create_user('u1', 'u1@e.com', 'pass-phrase-99')
        ProductReview.objects.create(
            product=self.product, user=user, rating=5, title='T', body='B',
        )
        ProductReview.objects.create(
            product=self.product, user=User.objects.create_user('u2', 'u2@e.com', 'pass-phrase-99'),
            rating=3, title='T', body='B', approved=False,
        )
        self.assertEqual(self.product.review_count(), 1)


class ProductVariantModelTests(TestCase):
    """``ProductVariant`` model methods."""

    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(name='Test', slug='test')
        cls.product = Product.objects.create(
            category=cls.category,
            name='Widget',
            slug='widget',
            price=Decimal('25.00'),
        )

    def test_str_combines_product_and_name(self):
        v = ProductVariant.objects.create(
            product=self.product, name='Red', sku='W-RED',
        )
        self.assertEqual(str(v), 'Widget — Red')

    def test_unit_price_uses_override(self):
        v = ProductVariant.objects.create(
            product=self.product, name='Red', sku='W-RED', price=Decimal('30.00'),
        )
        self.assertEqual(v.unit_price(), Decimal('30.00'))

    def test_unit_price_falls_back_to_product_price(self):
        v = ProductVariant.objects.create(
            product=self.product, name='Red', sku='W-RED',
        )
        self.assertEqual(v.unit_price(), Decimal('25.00'))

    def test_untracked_variant_is_in_stock(self):
        v = ProductVariant.objects.create(
            product=self.product, name='Red', sku='W-RED', stock=None,
        )
        self.assertTrue(v.is_in_stock())

    def test_zero_stock_variant_is_out_of_stock(self):
        v = ProductVariant.objects.create(
            product=self.product, name='Red', sku='W-RED', stock=0,
        )
        self.assertFalse(v.is_in_stock())
