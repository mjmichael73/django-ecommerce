"""Wish list model tests (card cc26292g)."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from shop.models import Category, Product, ProductVariant
from wishlist.models import WishList, WishListItem

User = get_user_model()


def _product(name='Mug', price='12.00', available=True):
    category, _ = Category.objects.get_or_create(
        slug='home', defaults={'name': 'Home'},
    )
    return Product.objects.create(
        category=category, name=name, slug=name.lower().replace(' ', '-'),
        price=Decimal(price), available=available,
    )


class WishListModelFixture(TestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            'shopper', 's@example.com', 'complex-pass-phrase-99',
        )
        self.product = _product()


class WishListModelTests(WishListModelFixture, TestCase):
    def test_one_list_per_user_auto_created(self):
        wl = WishList.objects.create(user=self.user)
        self.assertEqual(wl.user, self.user)
        with self.assertRaises(IntegrityError):
            WishList.objects.create(user=self.user)

    def test_add_creates_new_line(self):
        wl = WishList.objects.create(user=self.user)
        item, created = wl.add(self.product, quantity=2)
        self.assertTrue(created)
        self.assertEqual(item.quantity, 2)
        self.assertEqual(self.product.price * 2, item.line_total())

    def test_add_duplicate_bumps_quantity_no_second_row(self):
        wl = WishList.objects.create(user=self.user)
        wl.add(self.product, quantity=1)
        item, created = wl.add(self.product, quantity=3)
        self.assertFalse(created)
        self.assertEqual(item.quantity, 4)
        self.assertEqual(WishListItem.objects.filter(list=wl).count(), 1)

    def test_add_variant_and_plain_product_are_distinct_lines(self):
        wl = WishList.objects.create(user=self.user)
        variant = ProductVariant.objects.create(
            product=self.product, name='Big', sku='B1', price=Decimal('5.00'),
        )
        plain, _ = wl.add(self.product, quantity=1)
        varline, _ = wl.add(self.product, quantity=1, variant=variant)
        self.assertNotEqual(plain.pk, varline.pk)
        self.assertIsNone(plain.variant)
        self.assertEqual(varline.variant, variant)
        self.assertEqual(WishListItem.objects.filter(list=wl).count(), 2)

    def test_has_product_true_for_plain_line(self):
        wl = WishList.objects.create(user=self.user)
        wl.add(self.product)
        self.assertTrue(wl.has_product(self.product))

    def test_remove_owns_line(self):
        wl = WishList.objects.create(user=self.user)
        wl.add(self.product, quantity=3)
        self.assertEqual(wl.remove(self.product), 1)
        self.assertFalse(wl.items.filter(product=self.product).exists())

    def test_remove_absent_line_is_noop(self):
        wl = WishList.objects.create(user=self.user)
        self.assertEqual(wl.remove(self.product), 0)

    def test_unit_price_falls_back_to_product(self):
        wl = WishList.objects.create(user=self.user)
        item, _ = wl.add(self.product)
        self.assertEqual(item.unit_price, self.product.price)

    def test_unit_price_uses_variant(self):
        wl = WishList.objects.create(user=self.user)
        variant = ProductVariant.objects.create(
            product=self.product, name='Big', sku='B1', price=Decimal('5.00'),
        )
        item, _ = wl.add(self.product, variant=variant)
        self.assertEqual(item.unit_price, Decimal('5.00'))

    def test_line_total(self):
        wl = WishList.objects.create(user=self.user)
        item, _ = wl.add(self.product, quantity=3)
        self.assertEqual(item.line_total(), Decimal('36.00'))

    def test_product_delete_is_protected(self):
        wl = WishList.objects.create(user=self.user)
        wl.add(self.product)
        with self.assertRaises(ProtectedError):
            self.product.delete()
        self.assertTrue(WishList.objects.filter(user=self.user).exists())
        self.assertEqual(WishListItem.objects.count(), 1)

    def test_deleting_user_cascades_wishlist(self):
        wl = WishList.objects.create(user=self.user)
        wl.add(self.product)
        user_id = self.user.id
        self.user.delete()
        self.assertFalse(WishList.objects.filter(user_id=user_id).exists())


class WishListItemAvailabilityTests(WishListModelFixture, TestCase):
    def test_unavailable_product_line_is_dead(self):
        self.product.available = False
        self.product.save()
        wl = WishList.objects.create(user=self.user)
        item, _ = wl.add(self.product)
        self.assertFalse(item.is_available())

    def test_delisted_variant_line_is_dead(self):
        variant = ProductVariant.objects.create(
            product=self.product, name='Big', sku='B1', price=Decimal('5.00'),
            available=False,
        )
        wl = WishList.objects.create(user=self.user)
        item, _ = wl.add(self.product, variant=variant)
        self.assertFalse(item.is_available())

    def test_available_variant_line_is_alive(self):
        variant = ProductVariant.objects.create(
            product=self.product, name='Big', sku='B1', price=Decimal('5.00'),
            available=True,
        )
        wl = WishList.objects.create(user=self.user)
        item, _ = wl.add(self.product, variant=variant)
        self.assertTrue(item.is_available())
