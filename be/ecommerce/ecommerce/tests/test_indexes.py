"""Database index coverage (card a275hbca).

Verifies that strategic indexes declared on the models actually exist on the
model's ``Meta.indexes`` so they survive a ``makemigrations`` / ``migrate``
round-trip. This is a schema-level guard: the indexes themselves are created by
the migrations, but asserting on ``_meta.indexes`` confirms the model layer
declares them and that migrations won't drift from the declared intent.
"""
from django.db import models
from django.test import TestCase

from coupons.models import Coupon
from orders.models import OrderItem
from shop.models import Product, ProductVariant


def _index_fields(model):
    """Return a dict mapping a tuple of index field names to the Index object."""
    return {tuple(i.fields): i for i in model._meta.indexes}


class ProductIndexTests(TestCase):
    def test_category_available_index_exists(self):
        fields_map = _index_fields(Product)
        self.assertIn(('category', 'available'), fields_map)
        index = fields_map[('category', 'available')]
        self.assertIsInstance(index, models.Index)

    def test_price_index_exists(self):
        fields_map = _index_fields(Product)
        self.assertIn(('price',), fields_map)
        index = fields_map[('price',)]
        self.assertIsInstance(index, models.Index)

    def test_preexisting_indexes_still_present(self):
        fields_map = _index_fields(Product)
        self.assertIn(('id', 'slug'), fields_map)
        self.assertIn(('name',), fields_map)
        self.assertIn(('-created',), fields_map)


class OrderItemIndexTests(TestCase):
    def test_order_product_index_exists(self):
        fields_map = _index_fields(OrderItem)
        self.assertIn(('order', 'product'), fields_map)
        index = fields_map[('order', 'product')]
        self.assertIsInstance(index, models.Index)


class CouponIndexTests(TestCase):
    def test_code_is_unique_and_implicitly_indexed(self):
        """``code`` is ``unique=True``: Django auto-creates a unique index and
        exposes it via ``unique_together``-free ``_meta`` as a field with
        ``unique=True``. No explicit ``Meta.indexes`` entry is needed — adding
        one would be a redundant no-op."""
        code_field = Coupon._meta.get_field('code')
        self.assertTrue(
            code_field.unique,
            'Coupon.code must remain unique=True so lookups by code are indexed.',
        )
        # The unique field shows up in the DB-level unique index list, not in
        # Meta.indexes. Confirm we deliberately did NOT add a redundant index.
        fields_map = _index_fields(Coupon)
        self.assertNotIn(('code',), fields_map)


class ProductVariantIndexTests(TestCase):
    def test_product_available_index_exists(self):
        fields_map = _index_fields(ProductVariant)
        self.assertIn(('product', 'available'), fields_map)
        index = fields_map[('product', 'available')]
        self.assertIsInstance(index, models.Index)

    def test_preexisting_available_index_still_present(self):
        fields_map = _index_fields(ProductVariant)
        self.assertIn(('available',), fields_map)
