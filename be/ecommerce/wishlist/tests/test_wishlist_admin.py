"""Wish list admin tests (card cc26292g).

Covers the admin surfaces registered in ``wishlist.admin``:
  * WishList admin list/detail rendering.
  * The inline items render with availability status.
  * WishListItem standalone changelist search/filter.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from shop.models import Category, Product
from wishlist.models import WishList

User = get_user_model()


def _product(name='Mug', price='12.00', available=True):
    category, _ = Category.objects.get_or_create(
        slug='home', defaults={'name': 'Home'},
    )
    return Product.objects.create(
        category=category, name=name, slug=name.lower().replace(' ', '-'),
        price=Decimal(price), available=available,
    )


class WishlistAdminTests(TestCase):
    def setUp(self):
        super().setUp()
        self.staff = User.objects.create_superuser(
            'admin', 'admin@example.com', 'complex-pass-phrase-99',
        )
        self.client.force_login(self.staff)
        self.user = User.objects.create_user(
            'shopper', 's@example.com', 'complex-pass-phrase-99',
        )
        self.product = _product()
        self.wish_list = WishList.objects.create(user=self.user)
        self.wish_list.add(self.product, quantity=2)

    def test_wishlist_changelist_renders(self):
        resp = self.client.get(reverse('admin:wishlist_wishlist_changelist'))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('shopper', html)

    def test_wishlist_change_shows_inline_items(self):
        resp = self.client.get(
            reverse('admin:wishlist_wishlist_change', args=[self.wish_list.pk])
        )
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn(self.product.name, html)
        self.assertIn('icon-yes.svg', html)  # availability cell

    def test_wishlistitem_changelist_renders(self):
        resp = self.client.get(reverse('admin:wishlist_wishlistitem_changelist'))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn(self.product.name, html)
        self.assertIn('Customer', html)

    def test_wishlistitem_search_by_user(self):
        resp = self.client.get(
            reverse('admin:wishlist_wishlistitem_changelist')
            + f'?q={self.user.username}'
        )
        self.assertEqual(resp.status_code, 200)
