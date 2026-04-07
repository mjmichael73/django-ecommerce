from datetime import timedelta

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from coupons.models import Coupon


class CouponApplyViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.url = reverse('coupons:apply')
        now = timezone.now()
        self.valid = Coupon.objects.create(
            code='SPRING25',
            valid_from=now - timedelta(days=1),
            valid_to=now + timedelta(days=30),
            discount=25,
            active=True,
        )

    def test_valid_code_case_insensitive_sets_session(self):
        response = self.client.post(self.url, {'code': 'spring25'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session.get('coupon_id'), self.valid.id)

    def test_unknown_code_clears_coupon_session(self):
        self.client.session['coupon_id'] = self.valid.id
        self.client.session.save()
        response = self.client.post(self.url, {'code': 'DOESNOTEXIST'})
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(self.client.session.get('coupon_id'))

    def test_inactive_coupon_not_applied(self):
        self.valid.active = False
        self.valid.save()
        response = self.client.post(self.url, {'code': 'SPRING25'})
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(self.client.session.get('coupon_id'))

    def test_expired_coupon_not_applied(self):
        now = timezone.now()
        self.valid.valid_to = now - timedelta(days=1)
        self.valid.valid_from = now - timedelta(days=10)
        self.valid.save()
        response = self.client.post(self.url, {'code': 'SPRING25'})
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(self.client.session.get('coupon_id'))

    def test_future_coupon_not_applied(self):
        now = timezone.now()
        self.valid.valid_from = now + timedelta(days=1)
        self.valid.valid_to = now + timedelta(days=30)
        self.valid.save()
        response = self.client.post(self.url, {'code': 'SPRING25'})
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(self.client.session.get('coupon_id'))
