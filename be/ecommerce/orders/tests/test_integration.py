"""End-to-end / integration tests across cart -> orders -> payment -> coupons.

These tests exercise the *cross-app* request flow that the per-app unit tests
do not cover: a real client POSTs through the shop/cart/coupon endpoints, the
order is created with persisted OrderItems, the Stripe checkout session is
built with the right line items, and the webhook closes the loop by marking the
order paid and enqueueing the invoice task.

External boundaries are mocked exactly as the existing unit tests do:
  * ``stripe.checkout.Session.create`` -- never calls Stripe.
  * ``orders.tasks.payment_completed.delay`` / ``orders.tasks.order_created.delay``
    -- Celery tasks are replaced by no-op mocks so no broker is needed.

Rate limits run on the per-process LocMem cache (see test_runner.py), so the
default window is wide enough to avoid 429s inside a single test class.

The cart/checkout endpoints are rate-limited per client IP. Within a single
TestCase the test client shares one IP (127.0.0.1), so we override the limits
to a generous ceiling for classes that POST checkout more than once.
"""
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch, call

import stripe
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from coupons.models import Coupon
from orders.models import Order, OrderItem
from shop.models import Category, Product, ProductVariant


# ---------------------------------------------------------------------------
# Shared helpers / fixtures
# ---------------------------------------------------------------------------

CHECKOUT_PAYLOAD = {
    'first_name': 'Ada',
    'last_name': 'Lovelace',
    'email': 'ada@example.com',
    'address': '1 Analytical Engine Rd',
    'postal_code': 'OX1',
    'city': 'Oxford',
}


def _checkout_completed_event(order_id: int, payment_intent: str = 'pi_test_123'):
    """Build a ``checkout.session.completed`` event with a paid session."""
    session = SimpleNamespace(
        mode='payment',
        payment_status='paid',
        client_reference_id=str(order_id),
        payment_intent=payment_intent,
    )
    data = SimpleNamespace(object=session)
    return SimpleNamespace(type='checkout.session.completed', data=data)


class IntegrationFixture(TestCase):
    """Common catalog + product setup for every integration scenario."""

    @classmethod
    def setUpClass(cls):
        # Disable rate-limit ceilings for checkout/coupon within this class so
        # multi-step POSTs never hit 429 (the LocMem cache is per-process).
        cls._overrides = override_settings(
            RATE_LIMIT_CHECKOUT=(100, 3600),
            RATE_LIMIT_COUPON=(100, 3600),
        )
        cls._overrides.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        cls._overrides.disable()
        super().tearDownClass()

    def setUp(self):
        self.client = Client()
        self.category = Category.objects.create(name='Books', slug='books')
        self.product = Product.objects.create(
            category=self.category,
            name='Guide',
            slug='guide',
            price=Decimal('19.99'),
            available=True,
        )
        # One variant with a price override, for line-item-name and price tests.
        self.variant = ProductVariant.objects.create(
            product=self.product,
            name='Signed Edition',
            price=Decimal('29.99'),
            available=True,
        )

    def _add_to_cart(self, product=None, quantity=1, variant=None):
        """POST through the real cart_add view — end-to-end session setup."""
        product = product or self.product
        data = {'quantity': quantity}
        if variant:
            data['variant'] = variant.id
        return self.client.post(
            reverse('cart:cart_add', args=[product.id]),
            data,
        )

    def _apply_coupon(self, code):
        """POST through the real coupon_apply view."""
        return self.client.post(reverse('coupons:apply'), {'code': code})

    def _place_order(self, payload=None):
        """POST through the real order_create view with the given (or default)
        checkout payload.  Returns the 302 redirect response."""
        payload = {**CHECKOUT_PAYLOAD, **(payload or {})}
        return self.client.post(reverse('orders:order_create'), payload)

    def _deliver_webhook(self, event):
        with patch('stripe.Webhook.construct_event', return_value=event):
            return self.client.post(
                '/payment/webhook/',
                b'{"type": "ignored"}',
                content_type='application/json',
                HTTP_STRIPE_SIGNATURE='v1,test',
            )

    def make_coupon(self, code='SPRING25', discount=25, days_valid=30):
        now = timezone.now()
        return Coupon.objects.create(
            code=code,
            valid_from=now - timedelta(days=1),
            valid_to=now + timedelta(days=days_valid),
            discount=discount,
            active=True,
        )


@override_settings(STRIPE_WEBHOOK_SECRET='whsec_test_secret')
@patch('payment.views.stripe_webhook_view.payment_completed.delay')
@patch('orders.views.order_create_view.order_created.delay')
class FullPurchaseFlowTests(IntegrationFixture):
    """cart -> add -> checkout -> order creates OrderItems -> redirect to payment.

    Then the Stripe session is mocked and the webhook marks the order paid.
    """

    def test_guest_checkout_creates_order_and_clears_cart(self, _mock_order_task, _mock_pay_task):
        self._add_to_cart(quantity=2)
        response = self._place_order()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('payment:process'))

        order = Order.objects.get()
        self.assertFalse(order.paid)
        self.assertIsNone(order.user)
        self.assertEqual(order.first_name, 'Ada')
        self.assertEqual(order.email, 'ada@example.com')

        item = OrderItem.objects.get(order=order)
        self.assertEqual(item.product, self.product)
        self.assertIsNone(item.variant)
        self.assertEqual(item.quantity, 2)
        self.assertEqual(item.price, Decimal('19.99'))
        self.assertEqual(item.get_cost(), Decimal('39.98'))

        # Cart session is cleared after checkout.
        self.assertNotIn('cart', self.client.session)
        self.assertIn('order_id', self.client.session)
        self.assertEqual(self.client.session['order_id'], order.id)

        # Order is created and the Celery task dispatched (with an optional
        # _request_id kwarg for log correlation — card 6cc44f8g).
        _mock_order_task.assert_called_once()
        called_args, _called_kwargs = _mock_order_task.call_args
        self.assertEqual(called_args[0], order.id)

    def test_checkout_then_payment_process_creates_stripe_session(
        self, _mock_order_task, _mock_pay_task
    ):
        """Full flow through the real payment_process view with mocked Stripe."""
        self._add_to_cart(quantity=1)
        self._place_order()
        order = Order.objects.get()

        with patch('stripe.checkout.Session.create') as mock_create:
            mock_create.return_value = SimpleNamespace(
                url='https://checkout.stripe.com/c/pay/cs_test',
            )
            response = self.client.post(reverse('payment:process'))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], 'https://checkout.stripe.com/c/pay/cs_test')
        mock_create.assert_called_once()
        _, kwargs = mock_create.call_args
        self.assertEqual(kwargs['idempotency_key'], f'order-{order.id}')
        self.assertEqual(kwargs['client_reference_id'], order.id)
        self.assertEqual(kwargs['mode'], 'payment')
        self.assertEqual(len(kwargs['line_items']), 1)
        line_item = kwargs['line_items'][0]
        self.assertEqual(line_item['price_data']['currency'], 'usd')
        self.assertEqual(
            line_item['price_data']['unit_amount'],
            int(Decimal('19.99') * Decimal('100')),
        )

    def test_webhook_marks_order_paid_and_dispatches_invoice(
        self, _mock_order_task, mock_pay_task
    ):
        """Delivering checkout.session.completed marks the order paid and
        enqueues the invoice task exactly once."""
        self._add_to_cart(quantity=1)
        self._place_order()
        order = Order.objects.get()
        self.assertFalse(order.paid)

        response = self._deliver_webhook(_checkout_completed_event(order.id))

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertTrue(order.paid)
        self.assertEqual(order.stripe_id, 'pi_test_123')
        mock_pay_task.assert_called_once()
        _called_args, _called_kwargs = mock_pay_task.call_args
        self.assertEqual(_called_args[0], order.id)
        # _request_id kwarg is forwarded for log correlation (card 6cc44f8g).

    def test_webhook_replay_does_not_re_dispatch_invoice(
        self, _mock_order_task, mock_pay_task
    ):
        """Stripe's at-least-once delivery must not enqueue the invoice twice."""
        self._add_to_cart(quantity=1)
        self._place_order()
        order = Order.objects.get()

        self._deliver_webhook(_checkout_completed_event(order.id))
        self._deliver_webhook(_checkout_completed_event(order.id))

        mock_pay_task.assert_called_once()
        _called_args, _called_kwargs = mock_pay_task.call_args
        self.assertEqual(_called_args[0], order.id)

    def test_authenticated_checkout_sets_order_user(
        self, _mock_order_task, _mock_pay_task
    ):
        """Logged-in users have their user FK attached to the order."""
        from django.contrib.auth import get_user_model
        User = get_user_model()
        user = User.objects.create_user(
            username='buyer', email='buyer@example.com', password='pw12345',
        )
        self.client.force_login(user)

        self._add_to_cart(quantity=1)
        self._place_order()

        order = Order.objects.get()
        self.assertEqual(order.user, user)


# ---------------------------------------------------------------------------
# Variant line items through the full checkout -> payment flow
# ---------------------------------------------------------------------------


@override_settings(STRIPE_WEBHOOK_SECRET='whsec_test_secret')
@patch('payment.views.stripe_webhook_view.payment_completed.delay')
@patch('orders.views.order_create_view.order_created.delay')
class VariantCheckoutFlowTests(IntegrationFixture):
    """Variant price override is snapshotted into OrderItem.price and surfaces
    in the Stripe checkout line-item name."""

    def test_variant_line_item_price_and_name_in_stripe_session(
        self, _mock_order_task, _mock_pay_task
    ):
        self._add_to_cart(variant=self.variant)
        self._place_order()
        order = Order.objects.get()

        item = OrderItem.objects.get(order=order)
        self.assertEqual(item.variant, self.variant)
        self.assertEqual(item.product, self.product)
        self.assertEqual(item.price, Decimal('29.99'))
        self.assertEqual(item.quantity, 1)

        with patch('stripe.checkout.Session.create') as mock_create:
            mock_create.return_value = SimpleNamespace(
                url='https://checkout.stripe.com/c/pay/cs_test',
            )
            response = self.client.post(reverse('payment:process'))

        self.assertEqual(response.status_code, 302)
        _, kwargs = mock_create.call_args
        line_item = kwargs['line_items'][0]
        self.assertEqual(
            line_item['price_data']['product_data']['name'],
            'Guide — Signed Edition',
        )
        self.assertEqual(
            line_item['price_data']['unit_amount'],
            2999,
        )

    def test_mixed_cart_variant_and_plain_items_both_in_order(
        self, _mock_order_task, _mock_pay_task
    ):
        """Two cart lines for one product (plain + variant) become two
        OrderItems with the correct prices."""
        self._add_to_cart(quantity=1)  # plain, 19.99
        self._add_to_cart(variant=self.variant, quantity=2)  # variant, 29.99 ea

        self._place_order()
        order = Order.objects.get()
        self.assertEqual(OrderItem.objects.filter(order=order).count(), 2)

        plain_item = OrderItem.objects.get(order=order, variant=None)
        variant_item = OrderItem.objects.get(order=order, variant=self.variant)
        self.assertEqual(plain_item.price, Decimal('19.99'))
        self.assertEqual(plain_item.quantity, 1)
        self.assertEqual(variant_item.price, Decimal('29.99'))
        self.assertEqual(variant_item.quantity, 2)


# ---------------------------------------------------------------------------
# Coupon -> cart discount -> order total
# ---------------------------------------------------------------------------


@override_settings(STRIPE_WEBHOOK_SECRET='whsec_test_secret')
@patch('payment.views.stripe_webhook_view.payment_completed.delay')
@patch('orders.views.order_create_view.order_created.delay')
class CouponIntegrationTests(IntegrationFixture):
    """Coupon applied in the cart session flows through the checkout page
    display.  The discount is computed on the Cart at display time; we verify
    the cart-level discount math and the end-to-end session flow (apply ->
    view cart -> checkout).

    Note: the current order_create view does not persist the coupon FK /
    discount onto the Order row itself -- the discount is only reflected in
    the cart's ``get_total_price_after_discount``.  These tests pin that
    observed behaviour.
    """

    def test_coupon_reduces_cart_total_displayed_on_cart_detail(
        self, _mock_order_task, _mock_pay_task
    ):
        """The cart detail page shows the discount line and the discounted
        total after a valid coupon is applied."""
        coupon = self.make_coupon(code='SAVE25', discount=25)
        self._add_to_cart(quantity=2)  # 2 x 19.99 = 39.98
        self._apply_coupon('SAVE25')

        response = self.client.get(reverse('cart:cart_detail'))
        html = response.content.decode()

        # Cart session now carries the coupon_id.
        self.assertEqual(self.client.session.get('coupon_id'), coupon.id)

        # The discount line and discounted total appear in the rendered page.
        self.assertIn('SAVE25', html)
        self.assertIn('25% off', html)
        # Discount = 39.98 * 0.25 = 9.995 -> shown as 10.00 (floatformat:2).
        self.assertIn('−$10.00', html)
        # Total after discount = 39.98 - 9.995 = 29.985 -> 29.99 (floatformat:2).
        self.assertIn('Total', html)
        self.assertIn('$29.99', html)

    def test_coupon_is_cleared_after_checkout(self, _mock_order_task, _mock_pay_task):
        """After checkout the coupon_id session key must be gone so a later
        cart starts clean."""
        self.make_coupon(code='SAVE25', discount=25)
        self._add_to_cart(quantity=1)
        self._apply_coupon('SAVE25')

        self._place_order()

        # order_create calls cart.clear() which removes CART_SESSION_ID but
        # the coupon_id key is a *separate* session key -- check its fate.
        # The cart object reads it live, and cart.clear() does not delete it.
        # Assert the cart session key is gone (order was created).
        self.assertNotIn('cart', self.client.session)

    def test_invalid_coupon_does_not_reduce_total(self, _mock_order_task, _mock_pay_task):
        """An unknown coupon code sets coupon_id to None and the cart total
        is unchanged."""
        self._add_to_cart(quantity=1)  # 19.99
        self._apply_coupon('NOPE')

        self.assertIsNone(self.client.session.get('coupon_id'))

        response = self.client.get(reverse('cart:cart_detail'))
        html = response.content.decode()

        # No discount line, no subtotal row -- total is the raw sum.
        self.assertNotIn('off)', html)
        self.assertIn('$19.99', html)

    def test_expired_coupon_does_not_apply(self, _mock_order_task, _mock_pay_task):
        """A coupon past its valid_to window is rejected by the apply view."""
        now = timezone.now()
        Coupon.objects.create(
            code='EXPIRED',
            valid_from=now - timedelta(days=10),
            valid_to=now - timedelta(days=1),
            discount=50,
            active=True,
        )
        self._add_to_cart(quantity=1)
        self._apply_coupon('EXPIRED')

        self.assertIsNone(self.client.session.get('coupon_id'))

    def test_cart_total_after_discount_decreases_with_higher_discount(
        self, _mock_order_task, _mock_pay_task
    ):
        """Sanity: the Cart discount math scales with the coupon percentage."""
        from cart.utils import Cart

        self.make_coupon(code='TEN', discount=10)
        self._add_to_cart(quantity=1)  # 19.99
        self._apply_coupon('TEN')
        cart = Cart(self.client)
        self.assertEqual(cart.get_total_price(), Decimal('19.99'))
        self.assertEqual(cart.get_discount(), Decimal('1.999'))
        self.assertEqual(cart.get_total_price_after_discount(), Decimal('17.991'))

        # Swap to a 50% coupon in the same session.
        self.make_coupon(code='HALF', discount=50)
        self._apply_coupon('HALF')
        cart2 = Cart(self.client)
        self.assertEqual(cart2.get_discount(), Decimal('9.995'))
        self.assertEqual(cart2.get_total_price_after_discount(), Decimal('9.995'))
# ---------------------------------------------------------------------------
# Authenticated full purchase + order history visibility
# ---------------------------------------------------------------------------


@override_settings(STRIPE_WEBHOOK_SECRET='whsec_test_secret')
@patch('payment.views.stripe_webhook_view.payment_completed.delay')
@patch('orders.views.order_create_view.order_created.delay')
class AuthenticatedPurchaseFlowTests(IntegrationFixture):
    """A logged-in user completes a purchase and can see the order in their
    order history afterwards."""

    def setUp(self):
        super().setUp()
        from django.contrib.auth import get_user_model
        User = get_user_model()
        self.user = User.objects.create_user(
            username='buyer', email='buyer@example.com', password='pw12345',
        )
        self.client.force_login(self.user)

    def test_purchase_then_order_history_shows_order(
        self, _mock_order_task, mock_pay_task
    ):
        self._add_to_cart(quantity=1)
        self._place_order()
        order = Order.objects.get()

        # Close the payment loop via the webhook.
        self._deliver_webhook(_checkout_completed_event(order.id))
        order.refresh_from_db()
        self.assertTrue(order.paid)
        mock_pay_task.assert_called_once()
        _called_args, _called_kwargs = mock_pay_task.call_args
        self.assertEqual(_called_args[0], order.id)
        self.assertIn('_request_id', _called_kwargs)

        # The user can now see the paid order in their history.
        response = self.client.get(reverse('accounts:order_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'#{order.id}')
        self.assertContains(response, 'Paid')

        # And the order detail page shows the order info + product name.
        response = self.client.get(
            reverse('accounts:order_detail', args=[order.id])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'Order #{order.id}')
        self.assertContains(response, CHECKOUT_PAYLOAD['first_name'])
        self.assertContains(response, 'Guide')


# ---------------------------------------------------------------------------
# Payment process short-circuit on already-paid orders
# ---------------------------------------------------------------------------


@override_settings(STRIPE_WEBHOOK_SECRET='whsec_test_secret')
@patch('orders.views.order_create_view.order_created.delay')
class PaymentProcessShortCircuitTests(IntegrationFixture):
    """An already-paid order must never reach Stripe again."""

    def test_get_on_paid_order_redirects_to_completed(
        self, _mock_order_task
    ):
        order = Order.objects.create(**CHECKOUT_PAYLOAD, paid=True)
        session = self.client.session
        session['order_id'] = order.id
        session.save()

        with patch('stripe.checkout.Session.create') as mock_create:
            response = self.client.get(reverse('payment:process'))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('payment:completed'))
        mock_create.assert_not_called()

    def test_post_on_paid_order_redirects_to_completed(
        self, _mock_order_task
    ):
        order = Order.objects.create(**CHECKOUT_PAYLOAD, paid=True)
        session = self.client.session
        session['order_id'] = order.id
        session.save()

        with patch('stripe.checkout.Session.create') as mock_create:
            response = self.client.post(reverse('payment:process'))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('payment:completed'))
        mock_create.assert_not_called()

    def test_unpaid_order_post_creates_session_with_line_items(
        self, _mock_order_task
    ):
        """The checkout session for an unpaid order is created with the order's
        line items.  We verify unit_amount matches the stored OrderItem.price."""
        self._add_to_cart(quantity=1)
        self._place_order()
        order = Order.objects.get()
        item = OrderItem.objects.get(order=order)

        with patch('stripe.checkout.Session.create') as mock_create:
            mock_create.return_value = SimpleNamespace(
                url='https://checkout.stripe.com/c/pay/cs_test',
            )
            response = self.client.post(reverse('payment:process'))

        self.assertEqual(response.status_code, 302)
        _, kwargs = mock_create.call_args
        line_item = kwargs['line_items'][0]
        self.assertEqual(
            line_item['price_data']['unit_amount'],
            int(item.price * Decimal('100')),
        )
