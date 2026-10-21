"""Unit tests for the Coupon model.

Covers ``__str__``, validator behaviour on the ``discount`` field, and the
``active`` boolean. These complement the view-level tests in
``test_coupon_apply.py``.
"""
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from coupons.models import Coupon


class CouponModelTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.valid_from = now - timedelta(days=1)
        self.valid_to = now + timedelta(days=1)

    def test_str_returns_code(self):
        coupon = Coupon.objects.create(
            code='SPRING25',
            valid_from=self.valid_from,
            valid_to=self.valid_to,
            discount=25,
            active=True,
        )
        self.assertEqual(str(coupon), 'SPRING25')

    def test_discount_zero_is_valid(self):
        coupon = Coupon(code='ZERO', valid_from=self.valid_from,
                        valid_to=self.valid_to, discount=0, active=True)
        coupon.full_clean()  # should not raise
        coupon.save()
        self.assertEqual(coupon.discount, 0)

    def test_discount_100_is_valid(self):
        coupon = Coupon(code='ALL', valid_from=self.valid_from,
                        valid_to=self.valid_to, discount=100, active=True)
        coupon.full_clean()
        coupon.save()
        self.assertEqual(coupon.discount, 100)

    def test_discount_negative_is_invalid(self):
        coupon = Coupon(code='NEG', valid_from=self.valid_from,
                        valid_to=self.valid_to, discount=-1, active=True)
        with self.assertRaises(ValidationError):
            coupon.full_clean()

    def test_discount_over_100_is_invalid(self):
        coupon = Coupon(code='OVER', valid_from=self.valid_from,
                        valid_to=self.valid_to, discount=101, active=True)
        with self.assertRaises(ValidationError):
            coupon.full_clean()

    def test_active_defaults_can_be_set_false(self):
        coupon = Coupon(code='INACTIVE', valid_from=self.valid_from,
                        valid_to=self.valid_to, discount=10, active=False)
        coupon.full_clean()
        coupon.save()
        self.assertFalse(coupon.active)
