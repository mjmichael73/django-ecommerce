"""Product variants (card g99ha567).

One free-text variant dimension per product, with an optional price override
(``price = NULL`` inherits the product price). The cart keeps its historical
plain-product shape (``str(product.id)`` keys, ``quantity``/``price`` entries)
and adds variant lines as separate ``'<product_id>:<variant_id>'`` lines, so
pre-variant sessions keep working untouched.
"""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse

from cart.forms import CartAddProductForm
from cart.utils import Cart
from orders.models import Order, OrderItem
from shop.models import Category, Product, ProductVariant

User = get_user_model()

CHECKOUT_PAYLOAD = {
    'first_name': 'Ada',
    'last_name': 'Lovelace',
    'email': 'ada@example.com',
    'address': '1 Analytical Engine Rd',
    'postal_code': 'OX1',
    'city': 'Oxford',
}


class ProductVariantFixture(TestCase):
    def setUp(self):
        self.client = Client()
        self.category = Category.objects.create(name='Home', slug='home')
        self.product = Product.objects.create(
            category=self.category,
            name='Ceramic Mug',
            slug='ceramic-mug',
            price=Decimal('42.00'),
        )
        self.red = ProductVariant.objects.create(
            product=self.product,
            name='Red / M',
            sku='MUG-RED-M',
            price=Decimal('49.99'),
        )
        self.plain_variant = ProductVariant.objects.create(
            product=self.product,
            name='Blue',
        )

    def make_request(self):
        return SimpleNamespace(session=self.client.session)


class ProductVariantModelTests(ProductVariantFixture):
    def test_unit_price_uses_explicit_override(self):
        self.assertEqual(self.red.unit_price(), Decimal('49.99'))

    def test_unit_price_falls_back_to_product_price(self):
        self.assertIsNone(self.plain_variant.price)
        self.assertEqual(self.plain_variant.unit_price(), self.product.price)

    def test_str_combines_product_and_variant_name(self):
        self.assertEqual(str(self.red), 'Ceramic Mug — Red / M')

    def test_many_blank_skus_are_allowed_on_one_product(self):
        blank_one = ProductVariant.objects.create(product=self.product, name='Small')
        blank_two = ProductVariant.objects.create(product=self.product, name='Large')

        self.assertEqual(blank_one.sku, '')
        self.assertEqual(blank_two.sku, '')
        self.assertEqual(self.product.variants.filter(sku='').count(), 3)

    def test_duplicate_non_blank_sku_is_rejected_per_product(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProductVariant.objects.create(
                    product=self.product,
                    name='Duplicate SKU',
                    sku='MUG-RED-M',
                )

    def test_same_sku_is_allowed_on_a_different_product(self):
        other = Product.objects.create(
            category=self.category,
            name='Ceramic Bowl',
            slug='ceramic-bowl',
            price=Decimal('20.00'),
        )
        variant = ProductVariant.objects.create(
            product=other,
            name='Red',
            sku='MUG-RED-M',
        )
        self.assertEqual(variant.sku, 'MUG-RED-M')

    def test_ordering_is_product_then_name(self):
        self.assertEqual(
            list(self.product.variants.values_list('name', flat=True)),
            ['Blue', 'Red / M'],
        )


class CartAddProductFormTests(ProductVariantFixture):
    def test_form_without_variants_keeps_the_historical_shape(self):
        form = CartAddProductForm()

        self.assertEqual(list(form.fields), ['quantity', 'override'])
        self.assertNotIn('variant', form.fields)

    def test_form_with_variants_prepends_optional_selector(self):
        form = CartAddProductForm(variants=self.product.variants.all())

        self.assertEqual(list(form.fields), ['variant', 'quantity', 'override'])
        self.assertFalse(form.fields['variant'].required)
        self.assertEqual(form.fields['variant'].empty_label, 'Default')

    def test_detail_page_renders_variant_select_when_variants_exist(self):
        response = self.client.get(self.product.get_absolute_url())
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn('name="variant"', html)
        self.assertIn('Red / M', html)
        self.assertIn('Default', html)

    def test_detail_page_has_no_variant_select_for_plain_product(self):
        plain = Product.objects.create(
            category=self.category,
            name='Plain Mug',
            slug='plain-mug',
            price=Decimal('15.00'),
        )
        html = self.client.get(plain.get_absolute_url()).content.decode()

        self.assertNotIn('name="variant"', html)

    def test_unavailable_variants_are_not_offered(self):
        self.red.available = False
        self.red.save()
        # Only the available "Blue" variant is left, so the selector still shows.
        html = self.client.get(self.product.get_absolute_url()).content.decode()

        self.assertIn('name="variant"', html)
        self.assertNotIn('Red / M', html)

    def test_product_with_only_unavailable_variants_has_no_selector(self):
        self.product.variants.update(available=False)
        html = self.client.get(self.product.get_absolute_url()).content.decode()

        self.assertNotIn('name="variant"', html)


class CartVariantTests(ProductVariantFixture):
    def test_plain_add_keeps_the_exact_entry_dict(self):
        cart = Cart(self.make_request())
        cart.add(self.product, quantity=2)

        entry = cart.cart[str(self.product.id)]
        self.assertEqual(set(entry.keys()), {'quantity', 'price'})
        self.assertEqual(entry['quantity'], 2)
        self.assertEqual(entry['price'], '42.00')

    def test_variant_add_creates_a_separate_keyed_line(self):
        cart = Cart(self.make_request())
        cart.add(self.product, quantity=1, variant=self.red)

        self.assertNotIn(str(self.product.id), cart.cart)
        entry = cart.cart[f'{self.product.id}:{self.red.id}']
        self.assertEqual(entry['quantity'], 1)
        self.assertEqual(entry['price'], '49.99')
        self.assertEqual(entry['variant_id'], self.red.id)
        self.assertEqual(entry['variant_name'], 'Red / M')

    def test_variant_without_price_uses_product_price(self):
        cart = Cart(self.make_request())
        cart.add(self.product, variant=self.plain_variant)

        entry = cart.cart[f'{self.product.id}:{self.plain_variant.id}']
        self.assertEqual(entry['price'], '42.00')

    def test_two_variants_of_one_product_are_two_lines(self):
        cart = Cart(self.make_request())
        cart.add(self.product, quantity=1, variant=self.red)
        cart.add(self.product, quantity=3, variant=self.plain_variant)

        self.assertEqual(len(cart.cart), 2)
        self.assertEqual(cart.cart[f'{self.product.id}:{self.plain_variant.id}']['quantity'], 3)

    def test_override_quantity_targets_only_that_variant_line(self):
        cart = Cart(self.make_request())
        cart.add(self.product, quantity=1, variant=self.red)
        cart.add(self.product, quantity=2, variant=self.plain_variant)

        cart.add(self.product, quantity=5, override_quantity=True, variant=self.red)

        self.assertEqual(cart.cart[f'{self.product.id}:{self.red.id}']['quantity'], 5)
        self.assertEqual(cart.cart[f'{self.product.id}:{self.plain_variant.id}']['quantity'], 2)

    def test_remove_targets_one_line_only(self):
        cart = Cart(self.make_request())
        cart.add(self.product, variant=self.red)
        cart.add(self.product, variant=self.plain_variant)

        cart.remove(self.product, variant=self.red)

        self.assertNotIn(f'{self.product.id}:{self.red.id}', cart.cart)
        self.assertIn(f'{self.product.id}:{self.plain_variant.id}', cart.cart)

    def test_remove_plain_line_leaves_variant_lines(self):
        cart = Cart(self.make_request())
        cart.add(self.product)
        cart.add(self.product, variant=self.red)

        cart.remove(self.product)

        self.assertIn(f'{self.product.id}:{self.red.id}', cart.cart)
        self.assertNotIn(str(self.product.id), cart.cart)

    def test_iter_attaches_product_and_variant(self):
        cart = Cart(self.make_request())
        cart.add(self.product, variant=self.red)

        items = list(cart)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['product'], self.product)
        self.assertEqual(items[0]['variant'], self.red)
        self.assertEqual(items[0]['price'], Decimal('49.99'))
        self.assertEqual(items[0]['total_price'], Decimal('49.99'))

    def test_iter_of_plain_line_has_no_variant(self):
        cart = Cart(self.make_request())
        cart.add(self.product, quantity=2)

        item = list(cart)[0]

        self.assertEqual(item['product'], self.product)
        self.assertNotIn('variant', item)
        self.assertEqual(item['total_price'], Decimal('84.00'))

    def test_totals_sum_plain_and_variant_lines(self):
        cart = Cart(self.make_request())
        cart.add(self.product, quantity=2)
        cart.add(self.product, quantity=1, variant=self.red)

        # 2 × 42.00 + 1 × 49.99
        self.assertEqual(cart.get_total_price(), Decimal('133.99'))
        self.assertEqual(len(cart), 3)


class CartVariantViewTests(ProductVariantFixture):
    def test_cart_add_with_variant_creates_variant_line(self):
        response = self.client.post(
            reverse('cart:cart_add', args=[self.product.id]),
            {'quantity': 1, 'variant': self.red.id},
        )

        self.assertEqual(response.status_code, 302)
        entry = self.client.session['cart'][f'{self.product.id}:{self.red.id}']
        self.assertEqual(entry['variant_id'], self.red.id)

    def test_cart_add_without_variant_keeps_plain_line(self):
        self.client.post(
            reverse('cart:cart_add', args=[self.product.id]),
            {'quantity': 2},
        )

        entry = self.client.session['cart'][str(self.product.id)]
        self.assertEqual(set(entry.keys()), {'quantity', 'price'})

    def test_cross_product_variant_id_falls_back_to_plain_line(self):
        other = Product.objects.create(
            category=self.category,
            name='Other Mug',
            slug='other-mug',
            price=Decimal('10.00'),
        )
        foreign = ProductVariant.objects.create(product=other, name='Foreign', price=Decimal('11.00'))

        response = self.client.post(
            reverse('cart:cart_add', args=[self.product.id]),
            {'quantity': 1, 'variant': foreign.id},
        )

        self.assertEqual(response.status_code, 302)
        cart = self.client.session['cart']
        self.assertIn(str(self.product.id), cart)
        self.assertNotIn(f'{self.product.id}:{foreign.id}', cart)
        self.assertNotIn('variant_id', cart[str(self.product.id)])

    def test_unavailable_variant_id_falls_back_to_plain_line(self):
        self.red.available = False
        self.red.save()

        self.client.post(
            reverse('cart:cart_add', args=[self.product.id]),
            {'quantity': 1, 'variant': self.red.id},
        )

        self.assertIn(str(self.product.id), self.client.session['cart'])

    def test_non_numeric_variant_id_does_not_500(self):
        response = self.client.post(
            reverse('cart:cart_add', args=[self.product.id]),
            {'quantity': 1, 'variant': 'not-a-number'},
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn(str(self.product.id), self.client.session['cart'])

    def test_cart_detail_renders_variant_name_and_pass_through_id(self):
        self.client.post(
            reverse('cart:cart_add', args=[self.product.id]),
            {'quantity': 1, 'variant': self.red.id},
        )

        html = self.client.get(reverse('cart:cart_detail')).content.decode()

        self.assertIn('Ceramic Mug', html)
        self.assertIn('Red / M', html)
        self.assertIn(f'value="{self.red.id}"', html)

    def test_cart_update_by_variant_id_hits_the_right_line(self):
        self.client.post(
            reverse('cart:cart_add', args=[self.product.id]),
            {'quantity': 1, 'variant': self.red.id},
        )
        self.client.post(
            reverse('cart:cart_add', args=[self.product.id]),
            {'quantity': 1, 'variant': self.plain_variant.id},
        )

        self.client.post(
            reverse('cart:cart_add', args=[self.product.id]),
            {
                'quantity': 4,
                'override': 'on',
                'variant_id': self.red.id,
            },
        )

        cart = self.client.session['cart']
        self.assertEqual(cart[f'{self.product.id}:{self.red.id}']['quantity'], 4)
        self.assertEqual(cart[f'{self.product.id}:{self.plain_variant.id}']['quantity'], 1)

    def test_cart_remove_by_variant_id_removes_one_line(self):
        self.client.post(
            reverse('cart:cart_add', args=[self.product.id]),
            {'quantity': 1, 'variant': self.red.id},
        )
        self.client.post(
            reverse('cart:cart_add', args=[self.product.id]),
            {'quantity': 1, 'variant': self.plain_variant.id},
        )

        self.client.post(
            reverse('cart:cart_remove', args=[self.product.id]),
            {'variant_id': self.red.id},
        )

        cart = self.client.session['cart']
        self.assertNotIn(f'{self.product.id}:{self.red.id}', cart)
        self.assertIn(f'{self.product.id}:{self.plain_variant.id}', cart)


@patch('orders.views.order_create_view.order_created.delay')
class OrderVariantTests(ProductVariantFixture):
    def _fill_cart(self, variant=None):
        session = self.client.session
        if variant is None:
            session['cart'] = {
                str(self.product.id): {'quantity': 2, 'price': str(self.product.price)},
            }
        else:
            session['cart'] = {
                f'{self.product.id}:{variant.id}': {
                    'quantity': 1,
                    'price': str(variant.unit_price()),
                    'variant_id': variant.id,
                    'variant_name': variant.name,
                },
            }
        session.save()

    def test_checkout_records_variant_and_effective_price(self, _mock_delay):
        self._fill_cart(self.red)

        self.client.post(reverse('orders:order_create'), CHECKOUT_PAYLOAD)

        item = OrderItem.objects.get()
        self.assertEqual(item.variant, self.red)
        self.assertEqual(item.product, self.product)
        self.assertEqual(item.price, Decimal('49.99'))

    def test_checkout_without_variant_stores_null_variant(self, _mock_delay):
        self._fill_cart()

        self.client.post(reverse('orders:order_create'), CHECKOUT_PAYLOAD)

        item = OrderItem.objects.get()
        self.assertIsNone(item.variant)
        self.assertEqual(item.price, self.product.price)

    def test_order_history_survives_variant_deletion(self, _mock_delay):
        self._fill_cart(self.red)
        self.client.post(reverse('orders:order_create'), CHECKOUT_PAYLOAD)

        self.red.delete()

        item = OrderItem.objects.get()
        self.assertIsNone(item.variant)
        self.assertEqual(item.price, Decimal('49.99'))

    def test_checkout_with_deleted_variant_in_cart_stores_null_variant(self, _mock_delay):
        # The cart line still references a variant the admin deleted: checkout
        # must fall back to the price snapshot with a NULL variant, not 500 on
        # a dead FK id.
        self._fill_cart(self.red)
        stale_key = f'{self.product.id}:{self.red.id}'
        self.red.delete()
        self.assertIn(stale_key, self.client.session['cart'])

        response = self.client.post(reverse('orders:order_create'), CHECKOUT_PAYLOAD)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('payment:process'))
        self.assertEqual(Order.objects.count(), 1)
        item = OrderItem.objects.get()
        self.assertIsNone(item.variant)
        self.assertEqual(item.price, Decimal('49.99'))
        self.assertEqual(item.product, self.product)
        self.assertNotIn('cart', self.client.session)

    def test_checkout_mixed_cart_keeps_remaining_valid_variant(self, _mock_delay):
        session = self.client.session
        session['cart'] = {
            f'{self.product.id}:{self.red.id}': {
                'quantity': 1,
                'price': str(self.red.unit_price()),
                'variant_id': self.red.id,
                'variant_name': self.red.name,
            },
            f'{self.product.id}:{self.plain_variant.id}': {
                'quantity': 2,
                'price': str(self.plain_variant.unit_price()),
                'variant_id': self.plain_variant.id,
                'variant_name': self.plain_variant.name,
            },
        }
        session.save()
        self.red.delete()

        response = self.client.post(reverse('orders:order_create'), CHECKOUT_PAYLOAD)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(OrderItem.objects.count(), 2)
        stale_item = OrderItem.objects.get(price=Decimal('49.99'))
        self.assertIsNone(stale_item.variant)
        valid_item = OrderItem.objects.get(variant=self.plain_variant)
        self.assertEqual(valid_item.price, Decimal('42.00'))
        self.assertEqual(valid_item.quantity, 2)


class StripeVariantLineItemTests(ProductVariantFixture):
    def _order_with_items(self, variant=None):
        order = Order.objects.create(**CHECKOUT_PAYLOAD)
        OrderItem.objects.create(
            order=order,
            product=self.product,
            variant=variant,
            price=variant.unit_price() if variant else self.product.price,
            quantity=1,
        )
        session = self.client.session
        session['order_id'] = order.id
        session.save()
        return order

    @patch('stripe.checkout.Session.create')
    def test_variant_line_item_name_contains_variant(self, mock_create):
        mock_create.return_value = SimpleNamespace(url='https://checkout.stripe.com/c/pay/cs_test')
        self._order_with_items(self.red)

        self.client.post(reverse('payment:process'))

        _, kwargs = mock_create.call_args
        name = kwargs['line_items'][0]['price_data']['product_data']['name']
        self.assertEqual(name, 'Ceramic Mug — Red / M')
        self.assertEqual(kwargs['line_items'][0]['price_data']['unit_amount'], 4999)

    @patch('stripe.checkout.Session.create')
    def test_plain_line_item_name_is_unchanged(self, mock_create):
        mock_create.return_value = SimpleNamespace(url='https://checkout.stripe.com/c/pay/cs_test')
        self._order_with_items()

        self.client.post(reverse('payment:process'))

        _, kwargs = mock_create.call_args
        name = kwargs['line_items'][0]['price_data']['product_data']['name']
        self.assertEqual(name, 'Ceramic Mug')


class ProductVariantAdminTests(ProductVariantFixture):
    def test_product_change_form_contains_variant_inline(self):
        staff = User.objects.create_superuser('variant-admin', 'variant@example.com', 'password')
        self.client.force_login(staff)

        response = self.client.get(reverse('admin:shop_product_change', args=[self.product.id]))

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('variants-TOTAL_FORMS', html)
        self.assertIn('Red / M', html)
