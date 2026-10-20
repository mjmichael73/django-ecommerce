"""Tests for flag-only fraud detection — card 832hc4eg.

Covers the per-rule engine (orders/fraud.py), score capping, the
self-exclusion semantics of the velocity rules, the webhook hook-up
(flags persisted exactly once behind the idempotency gate, evaluation
failures never un-pay or 500), and the ``mark_rules_reviewed`` admin
action. Nothing here may block a payment — one test asserts the webhook
still answers 200 with a flagged order.
"""
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from coupons.models import Coupon
from orders.admin import mark_rules_reviewed
from orders.fraud import evaluate
from orders.models import Order, OrderItem
from shop.models import Category, Product


def _checkout_completed_event(order_id: int, payment_intent: str = 'pi_test_123'):
    session = SimpleNamespace(
        mode='payment',
        payment_status='paid',
        client_reference_id=str(order_id),
        payment_intent=payment_intent,
    )
    data = SimpleNamespace(object=session)
    return SimpleNamespace(type='checkout.session.completed', data=data)


class FraudTestMixin:
    def setUp(self):
        self.category = Category.objects.create(name='Books', slug='books')
        self.product = Product.objects.create(
            category=self.category,
            name='Guide',
            slug='guide',
            price=Decimal('10.00'),
        )

    def make_order(self, email='victim@example.com', user=None, coupon=None,
                   price=Decimal('10.00'), quantity=1, **kwargs):
        order = Order.objects.create(
            user=user,
            first_name='Test',
            last_name='User',
            email=email,
            address='1 St',
            postal_code='12345',
            city='City',
            coupon=coupon,
            **kwargs,
        )
        if price is not None:
            OrderItem.objects.create(
                order=order,
                product=self.product,
                price=price,
                quantity=quantity,
            )
        return order

    def make_coupon(self, code='SAVE10'):
        now = timezone.now()
        return Coupon.objects.create(
            code=code,
            valid_from=now - timedelta(days=1),
            valid_to=now + timedelta(days=1),
            discount=10,
            active=True,
        )


@override_settings(FRAUD_EMAIL_VELOCITY=2, FRAUD_WINDOW_HOURS=24)
class VelocityEmailRuleTests(FraudTestMixin, TestCase):
    def test_at_threshold_including_self_does_not_flag(self):
        """Self-exclusion: 2 orders sharing the email (self + 1 sibling) sit
        exactly at the threshold only if the order counted itself — it must
        not flag."""
        self.make_order()
        another = self.make_order(email='VICTIM@EXAMPLE.COM')
        score, flags = evaluate(another)
        self.assertNotIn('velocity_email', flags)
        self.assertEqual(score, 0)

    def test_one_more_sibling_flags_case_insensitively(self):
        self.make_order()
        self.make_order(email='VICTIM@EXAMPLE.COM')
        self.make_order(email='victim@example.com')
        score, flags = evaluate(Order.objects.first())
        self.assertIn('velocity_email', flags)

    def test_siblings_outside_window_do_not_count(self):
        order = self.make_order()
        stale = self.make_order(email='VICTIM@EXAMPLE.COM')
        Order.objects.filter(id=stale.id).update(
            created=timezone.now() - timedelta(hours=25)
        )
        score, flags = evaluate(order)
        self.assertNotIn('velocity_email', flags)
        self.assertEqual(score, 0)


@override_settings(FRAUD_LARGE_TOTAL=10)
class LargeTotalRuleTests(FraudTestMixin, TestCase):
    def test_fires_at_threshold(self):
        score, flags = evaluate(self.make_order())
        self.assertIn('large_total', flags)

    def test_no_flag_below_threshold(self):
        score, flags = evaluate(
            self.make_order(price=Decimal('9.99'))
        )
        self.assertNotIn('large_total', flags)
        self.assertEqual(score, 0)


class BulkQuantityRuleTests(FraudTestMixin, TestCase):
    @override_settings(FRAUD_BULK_QUANTITY=5)
    def test_fires_at_threshold_across_items(self):
        order = self.make_order(price=None)
        OrderItem.objects.create(order=order, product=self.product, price=Decimal('1.00'), quantity=3)
        OrderItem.objects.create(order=order, product=self.product, price=Decimal('1.00'), quantity=2)
        score, flags = evaluate(order)
        self.assertIn('bulk_quantity', flags)

    @override_settings(FRAUD_BULK_QUANTITY=5)
    def test_no_flag_below_threshold(self):
        score, flags = evaluate(
            self.make_order(quantity=4)
        )
        self.assertNotIn('bulk_quantity', flags)
        self.assertEqual(score, 0)


class NewAccountHighValueRuleTests(FraudTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(
            username='fresh', email='fresh@example.com', password='x',
        )

    @override_settings(FRAUD_NEW_ACCOUNT_HOURS=1, FRAUD_NEW_ACCOUNT_TOTAL=10)
    def test_fires_for_new_user_high_value(self):
        order = self.make_order(user=self.user, email='fresh@example.com')
        score, flags = evaluate(order)
        self.assertIn('new_account_high_value', flags)

    @override_settings(FRAUD_NEW_ACCOUNT_HOURS=1, FRAUD_NEW_ACCOUNT_TOTAL=10)
    def test_old_account_does_not_fire(self):
        get_user_model().objects.filter(id=self.user.id).update(
            date_joined=timezone.now() - timedelta(hours=2)
        )
        # The FK keeps the in-memory instance; refresh so the backdated
        # date_joined is visible on the cached related object.
        self.user.refresh_from_db()
        order = self.make_order(user=self.user, email='fresh@example.com')
        score, flags = evaluate(order)
        self.assertNotIn('new_account_high_value', flags)
        self.assertEqual(score, 0)

    @override_settings(FRAUD_NEW_ACCOUNT_HOURS=1, FRAUD_NEW_ACCOUNT_TOTAL=10)
    def test_low_value_order_does_not_fire(self):
        score, flags = evaluate(
            self.make_order(user=self.user, email='fresh@example.com',
                            price=Decimal('5.00'))
        )
        self.assertNotIn('new_account_high_value', flags)


class IdentityMismatchRuleTests(FraudTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(
            username='acc', email='acc@example.com', password='x',
        )

    def test_fires_when_checkout_email_differs(self):
        order = self.make_order(user=self.user, email='checkout@example.com')
        score, flags = evaluate(order)
        self.assertIn('identity_mismatch', flags)

    def test_matching_email_case_insensitive_does_not_fire(self):
        order = self.make_order(user=self.user, email='ACC@EXAMPLE.COM')
        score, flags = evaluate(order)
        self.assertNotIn('identity_mismatch', flags)
        self.assertEqual(score, 0)


@override_settings(FRAUD_COUPON_VELOCITY=2, FRAUD_WINDOW_HOURS=24)
class CouponVelocityRuleTests(FraudTestMixin, TestCase):
    def test_at_threshold_including_self_does_not_flag(self):
        coupon = self.make_coupon()
        self.make_order(coupon=coupon)
        self.make_order(coupon=coupon)
        score, flags = evaluate(Order.objects.first())
        self.assertNotIn('coupon_velocity', flags)
        self.assertEqual(score, 0)

    def test_one_more_sibling_flags(self):
        coupon = self.make_coupon()
        self.make_order(coupon=coupon)
        self.make_order(coupon=coupon)
        self.make_order(coupon=coupon)
        score, flags = evaluate(Order.objects.first())
        self.assertIn('coupon_velocity', flags)

    def test_order_without_coupon_does_not_fire(self):
        score, flags = evaluate(self.make_order())
        self.assertNotIn('coupon_velocity', flags)
        self.assertEqual(score, 0)


class EngineGuardTests(FraudTestMixin, TestCase):
    def test_guest_order_without_any_signals_fires_nothing(self):
        score, flags = evaluate(self.make_order())
        self.assertEqual(score, 0)
        self.assertEqual(flags, [])

    def test_score_is_capped_at_100(self):
        self.make_order()
        self.make_order()
        with override_settings(
            FRAUD_EMAIL_VELOCITY=1,
            FRAUD_LARGE_TOTAL=1,
            FRAUD_WEIGHT_VELOCITY_EMAIL=60,
            FRAUD_WEIGHT_LARGE_TOTAL=60,
        ):
            score, flags = evaluate(Order.objects.first())
        self.assertEqual(score, 100)
        self.assertEqual(flags, ['velocity_email', 'large_total'])

    def test_rule_exception_degrades_to_no_fire(self):
        """A rule that raises must never break evaluation."""
        order = self.make_order()

        def _boom(order, now):
            raise Exception('boom')

        with patch('orders.fraud.RULES', [('evil_rule', 10, _boom)]):
            score, flags = evaluate(order)
        self.assertEqual(score, 0)
        self.assertNotIn('evil_rule', flags)


@override_settings(
    STRIPE_WEBHOOK_SECRET='whsec_test_secret',
    FRAUD_LARGE_TOTAL=1,
    FRAUD_FLAG_THRESHOLD=10,
)
@patch('payment.views.stripe_webhook_view.payment_completed.delay')
class FraudWebhookTests(FraudTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()
        self.url = '/payment/webhook/'
        self.order = self.make_order()

    def _post_event(self, event):
        with patch(
            'stripe.Webhook.construct_event', return_value=event
        ):
            return self.client.post(
                self.url,
                b'{"type": "ignored"}',
                content_type='application/json',
                HTTP_STRIPE_SIGNATURE='v1,test',
            )

    def test_flags_persisted_exactly_once_and_replay_is_a_no_op(self, mock_task):
        response = self._post_event(_checkout_completed_event(self.order.id))
        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertTrue(self.order.paid)
        self.assertEqual(self.order.fraud_score, 30)
        self.assertTrue(self.order.is_flagged)
        self.assertEqual(self.order.fraud_notes, 'large_total')
        mock_task.assert_called_once_with(self.order.id)

        # Stripe replays the SAME event: paid/idempotency gate means the
        # score, flags and invoice dispatch must not change.
        response = self._post_event(_checkout_completed_event(self.order.id))
        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.fraud_score, 30)
        self.assertEqual(self.order.fraud_notes, 'large_total')
        self.assertTrue(self.order.is_flagged)
        self.assertEqual(mock_task.call_count, 1)

    def test_below_threshold_order_is_paid_but_not_flagged(self, mock_task):
        order = self.make_order(
            price=Decimal('0.50'), email='other@example.com'
        )
        response = self._post_event(_checkout_completed_event(order.id))
        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertTrue(order.paid)
        self.assertEqual(order.fraud_score, 0)
        self.assertFalse(order.is_flagged)
        self.assertEqual(order.fraud_notes, '')

    @patch('payment.views.stripe_webhook_view.evaluate',
           side_effect=Exception('fraud engine exploded'))
    def test_evaluation_crash_cannot_unpay_or_500(self, _mock_eval, mock_task):
        """Decision (§3): a fraud-evaluation failure is logged only — the
        order stays paid, the webhook still 200s, and the invoice dispatch
        below runs regardless of the evaluation's outcome."""
        response = self._post_event(_checkout_completed_event(self.order.id))
        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertTrue(self.order.paid)
        self.assertEqual(self.order.fraud_score, 0)
        self.assertFalse(self.order.is_flagged)
        mock_task.assert_called_once_with(self.order.id)

    def test_flagged_order_still_gets_200_flag_never_blocks(self, mock_task):
        """GUARD: nothing in this card blocks checkout/payment — a fast
        velocity order is flagged but the webhook still answers 200."""
        self.make_order(email='VICTIM@EXAMPLE.com')
        self.make_order(email='victim@EXAMPLE.COM')
        self.make_order(email='victim@example.com')
        response = self._post_event(_checkout_completed_event(self.order.id))
        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertTrue(self.order.paid)
        self.assertTrue(self.order.is_flagged)
        self.assertIn('velocity_email', self.order.fraud_notes)


class MarkRulesReviewedActionTests(FraudTestMixin, TestCase):
    def test_clears_flag_and_prefixes_notes_keeps_score(self):
        order = self.make_order(
            fraud_score=55, is_flagged=True,
            fraud_notes='velocity_email,large_total',
        )
        mark_rules_reviewed(None, None, Order.objects.filter(id=order.id))
        order.refresh_from_db()
        self.assertFalse(order.is_flagged)
        self.assertEqual(order.fraud_notes, 'reviewed: velocity_email,large_total')
        self.assertEqual(order.fraud_score, 55)

    def test_empty_notes_get_reviewed_prefix(self):
        order = self.make_order(fraud_score=0, is_flagged=False, fraud_notes='')
        mark_rules_reviewed(None, None, Order.objects.filter(id=order.id))
        order.refresh_from_db()
        self.assertEqual(order.fraud_notes, 'reviewed: ')

    def test_prefix_respects_max_length(self):
        order = self.make_order(
            is_flagged=True, fraud_notes='x' * 250,
        )
        mark_rules_reviewed(None, None, Order.objects.filter(id=order.id))
        order.refresh_from_db()
        self.assertEqual(len(order.fraud_notes), 250)
        self.assertTrue(order.fraud_notes.startswith('reviewed: '))
