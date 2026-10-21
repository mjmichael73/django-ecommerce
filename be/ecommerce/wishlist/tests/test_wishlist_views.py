"""Wish list view tests (card cc26292g).

Covers:
  * Anonymous access: bounced to login with ?next= on every endpoint.
  * Auth + ownership: a customer can only act on their own list lines.
  * Duplicate handling: re-add bumps quantity, no second row.
  * add / remove / clear / detail / add_to_cart behaviour.
  * Rate limiting on POST mutations (wishlist scope).
  * CSRF: POST without token is 403.
  * Edge cases: unavailable product is 404 on add; surviving dead lines.
"""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from cart.utils import Cart
from shop.models import Category, Product, ProductVariant
from wishlist.models import WishList, WishListItem

User = get_user_model()


def _category():
    return Category.objects.get_or_create(
        slug='home', defaults={'name': 'Home'},
    )[0]


def _product(name='Mug', price='12.00', available=True):
    return Product.objects.create(
        category=_category(), name=name, slug=name.lower().replace(' ', '-'),
        price=Decimal(price), available=available,
    )


def _variant(product, name='Big', sku='B1', price='5.00', available=True):
    return ProductVariant.objects.create(
        product=product, name=name, sku=sku, price=Decimal(price),
        available=available,
    )


class WishlistFixture(TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.client = Client()
        self.user = User.objects.create_user(
            'shopper', 's@example.com', 'complex-pass-phrase-99',
        )
        self.product = _product()
        self.other = User.objects.create_user(
            'other', 'o@example.com', 'complex-pass-phrase-99',
        )
        self.wish_list = WishList.objects.create(user=self.user)


class AnonymousAccessTests(WishlistFixture):
    def _assert_login_redirect(self, url_name, *args):
        url = reverse(url_name, args=args)
        # GET
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'].split('?')[0], '/accounts/login/')
        self.assertIn('next=', resp['Location'])
        # POST
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'].split('?')[0], '/accounts/login/')

    def test_detail_redirects_to_login(self):
        self._assert_login_redirect('wishlist:detail')

    def test_add_redirects_to_login(self):
        self._assert_login_redirect('wishlist:add', self.product.id)

    def test_remove_redirects_to_login(self):
        self._assert_login_redirect('wishlist:remove', self.product.id)

    def test_add_to_cart_redirects_to_login(self):
        self._assert_login_redirect('wishlist:add_to_cart', self.product.id)

    def test_clear_redirects_to_login(self):
        self._assert_login_redirect('wishlist:clear')


class WishlistAddTests(WishlistFixture):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self.url = reverse('wishlist:add', args=[self.product.id])

    def test_add_new_creates_line_and_redirects(self):
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], '/wishlist/')
        item = WishListItem.objects.get(product=self.product)
        self.assertEqual(item.list.user, self.user)
        self.assertEqual(item.quantity, 1)

    def test_add_existing_bumps_quantity_no_duplicate(self):
        self.wish_list.add(self.product, quantity=1)
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(WishListItem.objects.filter(list=self.wish_list).count(), 1)
        item = WishListItem.objects.get(product=self.product)
        self.assertEqual(item.quantity, 2)

    def test_add_with_quantity(self):
        resp = self.client.post(self.url, {'quantity': '5'})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(WishListItem.objects.get(product=self.product, list=self.wish_list).quantity, 5)

    def test_add_invalid_quantity_falls_back_to_one(self):
        resp = self.client.post(self.url, {'quantity': 'abc'})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(WishListItem.objects.get(product=self.product, list=self.wish_list).quantity, 1)

    def test_add_unavailable_product_is_404(self):
        self.product.available = False
        self.product.save()
        resp = self.client.post(reverse('wishlist:add', args=[self.product.id]))
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(WishListItem.objects.filter(product=self.product).exists())

    def test_add_variant_line(self):
        variant = _variant(self.product)
        resp = self.client.post(self.url, {'variant_id': variant.id})
        self.assertEqual(resp.status_code, 302)
        item = WishListItem.objects.get(product=self.product, variant=variant)
        self.assertEqual(item.list.user, self.user)

    def test_add_foreign_variant_falls_back_to_plain(self):
        # A variant id that doesn't belong to this product -> plain line.
        other_product = _product('Other', price='5.00')
        foreign_variant = _variant(other_product)
        resp = self.client.post(self.url, {'variant_id': foreign_variant.id})
        self.assertEqual(resp.status_code, 302)
        item = WishListItem.objects.get(
            product=self.product, variant__isnull=True, list=self.wish_list,
        )
        self.assertEqual(item.quantity, 1)


class WishlistRemoveTests(WishlistFixture):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_remove_own_line(self):
        self.wish_list.add(self.product)
        resp = self.client.post(reverse('wishlist:remove', args=[self.product.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(WishListItem.objects.filter(list=self.wish_list).exists())

    def test_remove_absent_line_is_noop(self):
        resp = self.client.post(reverse('wishlist:remove', args=[self.product.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(WishListItem.objects.filter(list=self.wish_list).exists())

    def test_remove_variant_line(self):
        variant = _variant(self.product)
        self.wish_list.add(self.product, variant=variant)
        resp = self.client.post(
            reverse('wishlist:remove', args=[self.product.id]),
            {'variant_id': variant.id},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(
            WishListItem.objects.filter(list=self.wish_list, variant=variant).exists()
        )

    def test_remove_unavailable_product_is_404(self):
        self.product.available = False
        self.product.save()
        resp = self.client.post(reverse('wishlist:remove', args=[self.product.id]))
        self.assertEqual(resp.status_code, 404)


class WishlistDetailTests(WishlistFixture):
    def test_detail_renders_empty_message(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('wishlist:detail'))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('Your wish list is empty', html)

    def test_detail_renders_lines(self):
        self.wish_list.add(self.product, quantity=2)
        self.client.force_login(self.user)
        resp = self.client.get(reverse('wishlist:detail'))
        html = resp.content.decode()
        self.assertIn(self.product.name, html)
        self.assertIn('Your wish list', html)
        self.assertIn('Move to cart', html)
        self.assertIn('Remove', html)

    def test_detail_shows_dead_badge_for_unavailable(self):
        self.wish_list.add(self.product)
        self.product.available = False
        self.product.save()
        self.client.force_login(self.user)
        resp = self.client.get(reverse('wishlist:detail'))
        html = resp.content.decode()
        self.assertIn('No longer available', html)


class WishlistAddToCartTests(WishlistFixture):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_move_own_line_to_cart_and_removes_from_list(self):
        self.wish_list.add(self.product, quantity=2)
        resp = self.client.post(
            reverse('wishlist:add_to_cart', args=[self.product.id])
        )
        self.assertEqual(resp.status_code, 302)
        # Removed from wish list.
        self.assertFalse(
            WishListItem.objects.filter(list=self.wish_list).exists()
        )
        # Added to cart session.
        self.assertIn(str(self.product.id), self.client.session.get('cart', {}))

    def test_move_not_on_list_errors(self):
        resp = self.client.post(
            reverse('wishlist:add_to_cart', args=[self.product.id])
        )
        self.assertEqual(resp.status_code, 302)
        # No line created.
        self.assertFalse(
            WishListItem.objects.filter(list=self.wish_list).exists()
        )


class WishlistClearTests(WishlistFixture):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_clear_empties_list(self):
        self.wish_list.add(self.product)
        resp = self.client.post(reverse('wishlist:clear'))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(WishListItem.objects.filter(list=self.wish_list).count(), 0)

    def test_clear_empty_list_is_message_only(self):
        resp = self.client.post(reverse('wishlist:clear'))
        self.assertEqual(resp.status_code, 302)


class WishlistOwnershipTests(WishlistFixture):
    """A customer must never touch another customer's list."""

    def test_add_does_not_touch_other_users_list(self):
        self.client.force_login(self.other)
        resp = self.client.post(reverse('wishlist:add', args=[self.product.id]))
        self.assertEqual(resp.status_code, 302)
        # Other user's list got the item; the fixture user's did not.
        self.assertTrue(
            WishListItem.objects.filter(list__user=self.other).exists()
        )
        self.assertFalse(
            WishListItem.objects.filter(list=self.wish_list).exists()
        )

    def test_remove_cannot_target_other_users_line(self):
        # The fixture user has a line; log in as OTHER and try to remove it.
        self.wish_list.add(self.product)
        self.assertTrue(WishListItem.objects.filter(list=self.wish_list).exists())
        self.client.force_login(self.other)
        resp = self.client.post(reverse('wishlist:remove', args=[self.product.id]))
        # Other has no list => a new empty one is created, remove is a no-op.
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            WishListItem.objects.filter(list=self.wish_list).exists()
        )

    def test_add_to_cart_cannot_reach_other_users_list(self):
        self.wish_list.add(self.product)
        self.client.force_login(self.other)
        resp = self.client.post(
            reverse('wishlist:add_to_cart', args=[self.product.id])
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            WishListItem.objects.filter(list=self.wish_list).exists()
        )


class WishlistCsrfTests(WishlistFixture):
    def setUp(self):
        super().setUp()
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.user)

    def test_add_requires_csrf_token(self):
        resp = self.client.post(
            reverse('wishlist:add', args=[self.product.id]),
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(resp.status_code, 403)

    def test_remove_requires_csrf_token(self):
        self.wish_list.add(self.product)
        resp = self.client.post(
            reverse('wishlist:remove', args=[self.product.id]),
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(resp.status_code, 403)


class WishlistRateLimitTests(WishlistFixture):
    @override_settings(RATE_LIMIT_WISHLIST=(2, 3600))
    def test_add_is_rate_limited(self):
        url = reverse('wishlist:add', args=[self.product.id])
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertEqual(self.client.post(url).status_code, 302)
        # 3rd within the window is rejected.
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 429)
        self.assertIn('Retry-After', resp)

    @override_settings(RATE_LIMIT_WISHLIST=(0, 0))
    def test_rate_limit_disabled(self):
        url = reverse('wishlist:add', args=[self.product.id])
        self.client.force_login(self.user)
        for _ in range(50):
            resp = self.client.post(url)
            self.assertEqual(resp.status_code, 302)

    def test_get_is_not_rate_limited(self):
        self.client.force_login(self.user)
        for _ in range(50):
            resp = self.client.get(reverse('wishlist:detail'))
            self.assertEqual(resp.status_code, 200)
