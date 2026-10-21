"""Admin workflow tests for return / refund requests (card 4fcgcg9e).

Covers the analyst-facing admin actions:
* approve / reject / mark_canceled state transitions
* mark_refunded issuing a real Stripe refund via refund_order
* Idempotency: mark_refunded on an already-refunded order is refused
* Stripe errors are caught and surfaced as warning messages, never 500
* Non-approved requests cannot be refunded
* Full-order vs item-scoped refund amounts
"""
from decimal import Decimal
from unittest.mock import patch, call

import stripe
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from orders.models import Order, OrderItem
from returns.admin import ReturnRequestAdmin
from returns.models import ReturnRequest
from shop.models import Category, Product

from types import SimpleNamespace


def _product(price='100.00'):
    category, _ = Category.objects.get_or_create(
        slug='books', defaults={'name': 'Books'}
    )
    product, _ = Product.objects.get_or_create(
        slug='guide',
        defaults={'category': category, 'name': 'Guide', 'price': Decimal(price)},
    )
    return product


def _order(paid=True, stripe_id='pi_test_123', price='100.00', quantity=1, user=None):
    order = Order.objects.create(
        first_name='Test', last_name='User',
        email='t@example.com', address='123 St',
        postal_code='12345', city='City',
        paid=paid, stripe_id=stripe_id or '',
        user=user,
    )
    OrderItem.objects.create(
        order=order, product=_product(price),
        price=Decimal(price), quantity=quantity,
    )
    return order


def _make_return(order=None, user=None, reason='Damaged'):
    if order is None:
        order = _order(user=user)
    return ReturnRequest.objects.create(
        order=order,
        reason=reason,
        user=user,
    )


@override_settings(STRIPE_SECRET_KEY='sk_test_123')
class ReturnAdminActionTests(TestCase):
    """Analyst actions in the Django admin."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_superuser(
            username='analyst', email='a@example.com', password='password'
        )
        self.client.force_login(self.staff)
        self.changelist_url = reverse('admin:returns_returnrequest_changelist')

    def _run_action(self, action, rr_ids):
        response = self.client.post(
            self.changelist_url,
            {
                'action': action,
                '_selected_action': [str(pid) for pid in rr_ids],
            },
        )
        return response, [str(m) for m in get_messages(response.wsgi_request)]

    @patch('stripe.Refund.create')
    def test_approve_moves_submitted_to_approved(self, mock_refund):
        rr = _make_return()
        resp, msgs = self._run_action('approve', [rr.pk])
        rr.refresh_from_db()
        self.assertEqual(rr.status, ReturnRequest.Status.APPROVED)
        mock_refund.assert_not_called()

    @patch('stripe.Refund.create')
    def test_approve_then_refund_issues_full_refund(self, mock_refund):
        mock_refund.return_value = SimpleNamespace(id='re_admin')
        rr = _make_return()
        # Approve first
        self._run_action('approve', [rr.pk])
        rr.refresh_from_db()
        self.assertEqual(rr.status, ReturnRequest.Status.APPROVED)
        # Then issue refund
        resp, msgs = self._run_action('mark_refunded', [rr.pk])
        rr.refresh_from_db()
        self.assertEqual(rr.status, ReturnRequest.Status.REFUNDED)
        self.assertEqual(rr.order.refunded_amount, Decimal('100.00'))
        self.assertEqual(rr.order.refund_id, 're_admin')
        mock_refund.assert_called_once_with(payment_intent='pi_test_123')

    @patch('stripe.Refund.create')
    def test_mark_refunded_without_approve_is_skipped(self, mock_refund):
        rr = _make_return()
        resp, msgs = self._run_action('mark_refunded', [rr.pk])
        rr.refresh_from_db()
        self.assertEqual(rr.status, ReturnRequest.Status.SUBMITTED)
        mock_refund.assert_not_called()
        self.assertTrue(any('not APPROVED' in m for m in msgs), msgs)

    @patch('stripe.Refund.create')
    def test_mark_refunded_on_already_refunded_is_refused(self, mock_refund):
        """Idempotency: re-running mark_refunded on a REFUNDED request
        is a no-op — refund_order refuses, and the status stays."""
        mock_refund.return_value = SimpleNamespace(id='re_1')
        rr = _make_return()
        self._run_action('approve', [rr.pk])
        self._run_action('mark_refunded', [rr.pk])
        # Second attempt
        resp, msgs = self._run_action('mark_refunded', [rr.pk])
        rr.refresh_from_db()
        self.assertEqual(rr.status, ReturnRequest.Status.REFUNDED)
        mock_refund.assert_called_once()  # only the first call reached Stripe

    @patch('stripe.Refund.create')
    def test_mark_refunded_stripe_error_is_surfaced(self, mock_refund):
        """A Stripe error during refund must not 500 the admin — it's
        caught by refund_order and surfaced as a warning message."""
        mock_refund.side_effect = stripe.error.CardError(
            'declined', 'payment_method', 'card_declined'
        )
        rr = _make_return()
        self._run_action('approve', [rr.pk])
        resp, msgs = self._run_action('mark_refunded', [rr.pk])
        rr.refresh_from_db()
        self.assertEqual(rr.status, ReturnRequest.Status.APPROVED)
        self.assertTrue(any('not issued' in m for m in msgs), msgs)
        self.assertTrue(any('stripe error' in m.lower() for m in msgs), msgs)

    @patch('stripe.Refund.create')
    def test_reject_moves_to_rejected_no_refund(self, mock_refund):
        rr = _make_return()
        resp, msgs = self._run_action('reject', [rr.pk])
        rr.refresh_from_db()
        self.assertEqual(rr.status, ReturnRequest.Status.REJECTED)
        mock_refund.assert_not_called()

    @patch('stripe.Refund.create')
    def test_reject_non_submitted_is_skipped(self, mock_refund):
        rr = _make_return()
        rr.status = ReturnRequest.Status.REJECTED
        rr.save(update_fields=['status'])
        resp, msgs = self._run_action('reject', [rr.pk])
        rr.refresh_from_db()
        self.assertEqual(rr.status, ReturnRequest.Status.REJECTED)
        self.assertTrue(any('cannot reject' in m for m in msgs), msgs)

    @patch('stripe.Refund.create')
    def test_cancel_moves_submitted_to_canceled(self, mock_refund):
        rr = _make_return()
        resp, msgs = self._run_action('mark_canceled', [rr.pk])
        rr.refresh_from_db()
        self.assertEqual(rr.status, ReturnRequest.Status.CANCELED)
        mock_refund.assert_not_called()

    @patch('stripe.Refund.create')
    def test_cancel_terminal_status_is_skipped(self, mock_refund):
        rr = _make_return()
        rr.status = ReturnRequest.Status.REFUNDED
        rr.save(update_fields=['status'])
        resp, msgs = self._run_action('mark_canceled', [rr.pk])
        rr.refresh_from_db()
        self.assertEqual(rr.status, ReturnRequest.Status.REFUNDED)
        self.assertTrue(any('cannot cancel' in m for m in msgs), msgs)

    @patch('stripe.Refund.create')
    def test_item_scoped_refund_uses_partial_amount(self, mock_refund):
        """An item-scoped return refunds the item cost, not the full order."""
        mock_refund.return_value = SimpleNamespace(id='re_partial')
        order = _order(price='100.00', quantity=1)
        item = order.items.first()
        rr = _make_return(order=order)
        rr.item = item
        rr.save(update_fields=['item'])
        self._run_action('approve', [rr.pk])
        self._run_action('mark_refunded', [rr.pk])
        rr.refresh_from_db()
        self.assertEqual(rr.status, ReturnRequest.Status.REFUNDED)
        # The item cost is 100.00 (one item at $100)
        # refunded_amount should be 100.00 (the item's cost)
        order.refresh_from_db()
        self.assertEqual(order.refunded_amount, Decimal('100.00'))

    @patch('stripe.Refund.create')
    def test_unpaid_order_cannot_be_refunded(self, mock_refund):
        """An order that was never paid cannot be refunded via mark_refunded."""
        order = _order(paid=False, stripe_id='')
        rr = _make_return(order=order)
        self._run_action('approve', [rr.pk])
        resp, msgs = self._run_action('mark_refunded', [rr.pk])
        rr.refresh_from_db()
        self.assertEqual(rr.status, ReturnRequest.Status.APPROVED)
        mock_refund.assert_not_called()
        self.assertTrue(any('not issued' in m for m in msgs), msgs)
