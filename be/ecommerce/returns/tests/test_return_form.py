"""Customer-facing return request form tests (card 4fcgcg9e).

Covers permissions, paid-only access, ownership enforcement, rate limiting,
and that no refund is ever issued on submission.
"""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from orders.models import Order, OrderItem
from returns.models import ReturnRequest
from shop.models import Category, Product


def _cat_prods():
    category, _ = Category.objects.get_or_create(
        slug='books', defaults={'name': 'Books'}
    )
    product, _ = Product.objects.get_or_create(
        slug='guide',
        defaults={'category': category, 'name': 'Guide', 'price': Decimal('50.00')},
    )
    return product


def _make_order(user=None, paid=True, stripe_id='pi_test_123', price='50.00', quantity=1):
    order = Order.objects.create(
        first_name='Test', last_name='User',
        email='t@example.com', address='123 St',
        postal_code='12345', city='City',
        paid=paid, stripe_id=stripe_id or '',
        user=user,
    )
    item = OrderItem.objects.create(
        order=order, product=_cat_prods(),
        price=Decimal(price), quantity=quantity,
    )
    return order, item


@override_settings(RATE_LIMIT_RETURN=(5, 3600))
class ReturnRequestFormAccessTests(TestCase):
    """Permissions and ownership for the customer return form."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.user = User.objects.create_user(
            username='alice', email='alice@example.com', password='password'
        )
        self.other = User.objects.create_user(
            username='bob', email='bob@example.com', password='password'
        )

    def _login(self, user):
        self.client.force_login(user)

    def test_get_requires_login(self):
        order, _ = _make_order(user=self.user)
        resp = self.client.get(reverse('accounts:order_return', args=[order.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse('accounts:login'), resp['Location'])

    def test_post_requires_login(self):
        order, _ = _make_order(user=self.user)
        resp = self.client.post(
            reverse('accounts:order_return', args=[order.pk]),
            {'reason': 'Damaged'},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse('accounts:login'), resp['Location'])

    def test_unpaid_order_returns_404(self):
        self._login(self.user)
        order, _ = _make_order(user=self.user, paid=False)
        resp = self.client.post(
            reverse('accounts:order_return', args=[order.pk]),
            {'reason': 'Damaged'},
        )
        self.assertEqual(resp.status_code, 404)

    def test_other_users_order_returns_404(self):
        self._login(self.other)
        order, _ = _make_order(user=self.user)
        resp = self.client.post(
            reverse('accounts:order_return', args=[order.pk]),
            {'reason': 'Damaged'},
        )
        self.assertEqual(resp.status_code, 404)

    def test_guest_order_not_accessible_by_guest_without_login(self):
        """An order placed by an anonymous guest has user=NULL; only that
        guest cannot return it via this flow since they can't log in to it."""
        order, _ = _make_order(user=None)
        resp = self.client.post(
            reverse('accounts:order_return', args=[order.pk]),
            {'reason': 'Damaged'},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse('accounts:login'), resp['Location'])

    @patch('stripe.Refund.create')
    def test_valid_post_creates_return_request_no_refund(self, mock_refund):
        self._login(self.user)
        order, _ = _make_order(user=self.user)
        resp = self.client.post(
            reverse('accounts:order_return', args=[order.pk]),
            {'reason': 'Damaged', 'detail': 'Item arrived broken'},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertRedirects(resp, reverse('accounts:return_list'))
        rr = ReturnRequest.objects.get()
        self.assertEqual(rr.order, order)
        self.assertEqual(rr.reason, 'Damaged')
        self.assertEqual(rr.detail, 'Item arrived broken')
        self.assertIsNone(rr.item)
        self.assertEqual(rr.status, ReturnRequest.Status.SUBMITTED)
        mock_refund.assert_not_called()

    @patch('stripe.Refund.create')
    def test_valid_post_with_item_scopes_request(self, mock_refund):
        self._login(self.user)
        order, item = _make_order(user=self.user)
        resp = self.client.post(
            reverse('accounts:order_return', args=[order.pk]),
            {'reason': 'Wrong color', 'item': item.pk},
        )
        self.assertEqual(resp.status_code, 302)
        rr = ReturnRequest.objects.get()
        self.assertEqual(rr.item, item)
        mock_refund.assert_not_called()

    def test_missing_reason_shows_form_error(self):
        self._login(self.user)
        order, _ = _make_order(user=self.user)
        resp = self.client.post(
            reverse('accounts:order_return', args=[order.pk]),
            {'reason': '', 'detail': 'No reason'},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFormError(resp.context['form'], 'reason', 'This field is required.')

    def test_item_from_other_order_rejected(self):
        self._login(self.user)
        order1, item1 = _make_order(user=self.user)
        order2, item2 = _make_order(user=self.user)
        # Post a return for order2 but submit item from order1
        resp = self.client.post(
            reverse('accounts:order_return', args=[order2.pk]),
            {'reason': 'OK', 'item': item1.pk},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(ReturnRequest.objects.exists())

    def test_rate_limit_blocks_after_limit(self):
        self._login(self.user)
        order, _ = _make_order(user=self.user)
        # RATE_LIMIT_RETURN = (5, 3600); 5 should succeed
        for i in range(5):
            resp = self.client.post(
                reverse('accounts:order_return', args=[order.pk]),
                {'reason': f'Damaged {i}'},
            )
            self.assertEqual(resp.status_code, 302, f'iteration {i} returned {resp.status_code}')
        # 6th should be rate-limited
        resp = self.client.post(
            reverse('accounts:order_return', args=[order.pk]),
            {'reason': 'Damaged 6'},
        )
        self.assertEqual(resp.status_code, 429)

    def test_rate_limit_allows_get(self):
        self._login(self.user)
        order, _ = _make_order(user=self.user)
        resp = self.client.get(reverse('accounts:order_return', args=[order.pk]))
        self.assertEqual(resp.status_code, 200)

    def test_rate_limit_disabled_when_period_zero(self):
        """When RATE_LIMIT_RETURN period is 0, rate limiting is off."""
        cache.clear()
        with override_settings(RATE_LIMIT_RETURN=(5, 0)):
            self._login(self.user)
            order, _ = _make_order(user=self.user)
            for i in range(10):
                resp = self.client.post(
                    reverse('accounts:order_return', args=[order.pk]),
                    {'reason': f'Damaged {i}'},
                )
                self.assertEqual(resp.status_code, 302, f'iteration {i} returned {resp.status_code}')


class ReturnRequestListTests(TestCase):
    """The customer-facing return request list page."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.user = User.objects.create_user(
            username='alice', email='alice@example.com', password='password'
        )
        self.client.force_login(self.user)

    def test_list_requires_login(self):
        self.client.logout()
        resp = self.client.get(reverse('accounts:return_list'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse('accounts:login'), resp['Location'])

    def test_list_shows_own_requests_only(self):
        order1, _ = _make_order(user=self.user)
        rr1 = ReturnRequest.objects.create(order=order1, reason='Damaged', user=self.user)
        # Another user's order + return request
        other = User.objects.create_user(username='bob', email='bob@example.com', password='p')
        order2, _ = _make_order(user=other)
        rr2 = ReturnRequest.objects.create(order=order2, reason='Other', user=other)

        resp = self.client.get(reverse('accounts:return_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, f'#{rr1.id}')
        self.assertNotContains(resp, f'#{rr2.id}')

    def test_list_empty_state(self):
        resp = self.client.get(reverse('accounts:return_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'no return requests')


class OrderDetailReturnLinkTests(TestCase):
    """The 'Request a return' link on the order detail page.

    An order is returnable when it is paid and has a positive un-refunded
    balance — a partial refund on one item must NOT hide the link for the
    remaining items (card 4fcgcg9e review finding).
    """

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.user = User.objects.create_user(
            username='alice', email='alice@example.com', password='password'
        )
        self.client.force_login(self.user)

    def test_return_link_shown_for_paid_fully_unrefunded_order(self):
        order, _ = _make_order(user=self.user, paid=True)
        resp = self.client.get(reverse('accounts:order_detail', args=[order.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Request a return')

    def test_return_link_hidden_for_unpaid_order(self):
        order, _ = _make_order(user=self.user, paid=False)
        resp = self.client.get(reverse('accounts:order_detail', args=[order.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Request a return')

    def test_return_link_hidden_when_fully_refunded(self):
        order, _ = _make_order(user=self.user, paid=True)
        # Simulate a full refund recorded on the order row.
        order.refunded_amount = Decimal('50.00')
        order.save(update_fields=['refunded_amount'])
        resp = self.client.get(reverse('accounts:order_detail', args=[order.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Request a return')

    def test_return_link_shown_after_partial_refund(self):
        """A partial refund leaves remaining items returnable."""
        order, item = _make_order(user=self.user, paid=True, price='50.00')
        # Record a partial refund of $10 (the item cost), leaving $40 to return.
        order.refunded_amount = Decimal('10.00')
        order.save(update_fields=['refunded_amount'])
        resp = self.client.get(reverse('accounts:order_detail', args=[order.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Request a return')
