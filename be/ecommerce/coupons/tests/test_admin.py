"""Unit tests for CouponAdmin configuration (coupons/admin.py)."""
from datetime import timedelta

from django.contrib import admin
from django.contrib.admin.sites import AdminSite
from django.test import TestCase
from django.utils import timezone

from coupons.admin import CouponAdmin
from coupons.models import Coupon


class CouponAdminConfigTests(TestCase):
    """Verify CouponAdmin registration and list configuration."""

    @classmethod
    def setUpTestData(cls):
        cls.site = AdminSite()
        cls.ma = CouponAdmin(Coupon, cls.site)

    def test_list_display_fields(self):
        # Access the class attribute directly to avoid deepcopy issues.
        self.assertEqual(
            list(CouponAdmin.list_display),
            ['code', 'valid_from', 'valid_to', 'discount', 'active'],
        )

    def test_list_filter_fields(self):
        self.assertEqual(
            list(CouponAdmin.list_filter),
            ['active', 'valid_from', 'valid_to'],
        )

    def test_search_fields(self):
        self.assertEqual(list(CouponAdmin.search_fields), ['code'])

    def test_model_registered(self):
        """CouponAdmin is the admin class registered for the Coupon model."""
        self.assertIs(admin.site._registry[Coupon].__class__, CouponAdmin)


class CouponAdminStrTests(TestCase):
    """CouponAdmin does not override __str__, so model-level __str__ is used."""

    def test_str_uses_code(self):
        now = timezone.now()
        coupon = Coupon.objects.create(
            code='ADMIN10',
            valid_from=now - timedelta(days=1),
            valid_to=now + timedelta(days=1),
            discount=10,
            active=True,
        )
        self.assertEqual(str(coupon), 'ADMIN10')
