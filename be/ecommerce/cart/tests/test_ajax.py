"""AJAX cart updates (card 77ce5899).

Tests that ``cart_add`` and ``cart_remove`` respond to AJAX
(``X-Requested-With: XMLHttpRequest`` or ``Accept: application/json``)
requests with a JSON contract that carries the updated cart total, item
count, and (when relevant) form errors / product name so the client can
update the cart badge and the detail table without a full reload.

Non-AJAX POSTs keep the original 302 → ``cart:cart_detail`` behaviour.
"""
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse

from cart.utils import Cart
from shop.models import Category, Product, ProductVariant


class AjaxCartFixture(TestCase):
    def setUp(self):
        self.client = Client()
        self.category = Category.objects.create(name='Home', slug='home')
        self.product = Product.objects.create(
            category=self.category,
            name='Ceramic Mug',
            slug='ceramic-mug',
            price=Decimal('42.00'),
        )
        self.variant = ProductVariant.objects.create(
            product=self.product,
            name='Red / M',
            sku='MUG-RED-M',
            price=Decimal('49.99'),
        )


class AjaxCartAddTests(AjaxCartFixture):
    """AJAX POST to cart_add returns JSON with total + quantity."""

    def test_ajax_add_returns_json_with_total_and_quantity(self):
        response = self.client.post(
            reverse('cart:cart_add', args=[self.product.id]),
            {'quantity': 2},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/json')
        body = response.json()
        self.assertTrue(body['ok'])
        self.assertEqual(body['quantity'], 2)
        # 2 × 42.00
        self.assertEqual(body['total'], '84.00')
        self.assertEqual(body['total_items'], 2)

    def test_ajax_add_with_variant_returns_variant_price_in_total(self):
        response = self.client.post(
            reverse('cart:cart_add', args=[self.product.id]),
            {'quantity': 1, 'variant': self.variant.id},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body['ok'])
        self.assertIn('Red / M', body['product_name'])
        self.assertEqual(body['total'], '49.99')
        self.assertEqual(body['total_items'], 1)

    def test_ajax_add_invalid_quantity_returns_errors_json(self):
        response = self.client.post(
            reverse('cart:cart_add', args=[self.product.id]),
            {'quantity': 0},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response['Content-Type'], 'application/json')
        body = response.json()
        self.assertFalse(body['ok'])
        self.assertIn('quantity', body['errors'])

    def test_ajax_add_unknown_product_returns_404_json(self):
        response = self.client.post(
            reverse('cart:cart_add', args=[999999]),
            {'quantity': 1},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response['Content-Type'], 'application/json')
        body = response.json()
        self.assertFalse(body['ok'])

    def test_ajax_add_accumulates_quantity(self):
        self.client.post(
            reverse('cart:cart_add', args=[self.product.id]),
            {'quantity': 2},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        response = self.client.post(
            reverse('cart:cart_add', args=[self.product.id]),
            {'quantity': 3, 'override': 'on'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        body = response.json()
        self.assertEqual(body['quantity'], 3)
        self.assertEqual(body['total'], '126.00')


class AjaxCartRemoveTests(AjaxCartFixture):
    """AJAX POST to cart_remove returns JSON with updated totals."""

    def test_ajax_remove_returns_json_with_updated_totals(self):
        # First add to cart (non-AJAX to set up state)
        self.client.post(
            reverse('cart:cart_add', args=[self.product.id]),
            {'quantity': 2},
        )

        response = self.client.post(
            reverse('cart:cart_remove', args=[self.product.id]),
            {},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body['ok'])
        self.assertEqual(body['total'], '0.00')
        self.assertEqual(body['total_items'], 0)

    def test_ajax_remove_variant_line_updates_totals(self):
        self.client.post(
            reverse('cart:cart_add', args=[self.product.id]),
            {'quantity': 1, 'variant': self.variant.id},
        )

        response = self.client.post(
            reverse('cart:cart_remove', args=[self.product.id]),
            {'variant_id': self.variant.id},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        body = response.json()
        self.assertTrue(body['ok'])
        self.assertEqual(body['total'], '0.00')
        self.assertEqual(body['total_items'], 0)

    def test_ajax_remove_unknown_product_returns_404_json(self):
        response = self.client.post(
            reverse('cart:cart_remove', args=[999999]),
            {},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertFalse(body['ok'])


class AjaxCartDetailTests(AjaxCartFixture):
    """AJAX GET to cart_detail returns JSON with cart summary."""

    def test_ajax_get_cart_detail_returns_json(self):
        self.client.post(
            reverse('cart:cart_add', args=[self.product.id]),
            {'quantity': 3},
        )

        response = self.client.get(
            reverse('cart:cart_detail'),
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/json')
        body = response.json()
        self.assertEqual(body['total_items'], 3)
        self.assertEqual(body['total'], '126.00')

    def test_ajax_get_empty_cart_returns_zero_totals(self):
        response = self.client.get(
            reverse('cart:cart_detail'),
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['total_items'], 0)
        self.assertEqual(body['total'], '0.00')


class NonAjaxCartCompatTests(AjaxCartFixture):
    """Non-AJAX POSTs keep the original 302 redirect behaviour."""

    def test_non_ajax_add_redirects_to_cart_detail(self):
        response = self.client.post(
            reverse('cart:cart_add', args=[self.product.id]),
            {'quantity': 1},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('cart:cart_detail'))

    def test_non_ajax_remove_redirects_to_cart_detail(self):
        self.client.post(
            reverse('cart:cart_add', args=[self.product.id]),
            {'quantity': 1},
        )

        response = self.client.post(
            reverse('cart:cart_remove', args=[self.product.id]),
            {},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('cart:cart_detail'))

    def test_non_ajax_get_cart_detail_is_html(self):
        response = self.client.get(reverse('cart:cart_detail'))

        self.assertEqual(response.status_code, 200)
        self.assertIn('text/html', response['Content-Type'])

    def test_accept_json_header_triggers_ajax_response(self):
        """The Accept: application/json header alone should trigger JSON."""
        response = self.client.post(
            reverse('cart:cart_add', args=[self.product.id]),
            {'quantity': 1},
            HTTP_ACCEPT='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/json')
        body = response.json()
        self.assertTrue(body['ok'])


class AjaxCartTemplateTests(AjaxCartFixture):
    """Templates include AJAX-enhancement data attributes."""

    def test_add_form_has_ajax_data_attributes(self):
        self.client.post(
            reverse('cart:cart_add', args=[self.product.id]),
            {'quantity': 1},
        )

        response = self.client.get(reverse('cart:cart_detail'))
        html = response.content.decode()

        # The add/update forms should carry a data attribute the JS can hook.
        self.assertIn('data-cart-add', html)

    def test_remove_forms_have_ajax_data_attributes(self):
        self.client.post(
            reverse('cart:cart_add', args=[self.product.id]),
            {'quantity': 1},
        )

        html = self.client.get(reverse('cart:cart_detail')).content.decode()
        self.assertIn('data-cart-remove', html)

    def test_cart_badge_has_update_targets(self):
        html = self.client.get(reverse('cart:cart_detail')).content.decode()
        self.assertIn('id="cart-badge"', html)
        self.assertIn('id="cart-total"', html)

    def test_ajax_js_script_is_included(self):
        html = self.client.get(reverse('cart:cart_detail')).content.decode()
        self.assertIn('cart-ajax.js', html)
