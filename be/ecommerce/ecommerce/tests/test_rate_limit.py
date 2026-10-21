"""Tests for POST rate limiting (card 25ahc7hg).

The decorator is exercised directly through ``RequestFactory`` (no DB) and
through the real coupon/checkout URLs through the Django test client.
``CACHES`` is forced to locmem so the suite never writes rate-limit keys to
the shared Redis instance, even when ``REDIS_CACHE_URL`` is set in the image.
"""
import time

from django.core.cache import cache
from django.http import HttpResponse
from django.test import (
    RequestFactory,
    SimpleTestCase,
    TestCase,
    override_settings,
)
from django.urls import reverse
from django.views.decorators.http import require_POST

from ecommerce.rate_limit import client_ip, rate_limit

LOCMEM_CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'rate-limit-tests',
    }
}


@rate_limit('checkout')
def _checkout_view(request):
    return HttpResponse('ok')


@rate_limit('coupon')
@require_POST
def _post_only_view(request):
    return HttpResponse('ok')


@override_settings(CACHES=LOCMEM_CACHES)
class RateLimitDecoratorTests(SimpleTestCase):
    """Fixed-window behaviour of the decorator itself."""

    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()
        self.ip = '10.1.2.3'

    def _post(self, ip=None):
        return _checkout_view(
            self.factory.post('/', REMOTE_ADDR=ip or self.ip)
        )

    @override_settings(RATE_LIMIT_CHECKOUT=(2, 60))
    def test_allows_up_to_limit_then_returns_429_with_retry_after(self):
        self.assertEqual(self._post().status_code, 200)
        self.assertEqual(self._post().status_code, 200)

        response = self._post()
        self.assertEqual(response.status_code, 429)
        self.assertIn('text/plain', response['Content-Type'])
        self.assertIn('Too many requests', response.content.decode())
        retry_after = int(response['Retry-After'])
        self.assertGreaterEqual(retry_after, 1)
        self.assertLessEqual(retry_after, 60)

    @override_settings(RATE_LIMIT_CHECKOUT=(1, 60))
    def test_clients_are_limited_independently(self):
        self.assertEqual(self._post('10.0.0.1').status_code, 200)
        self.assertEqual(self._post('10.0.0.1').status_code, 429)
        # A second client is unaffected by the first one's window.
        self.assertEqual(self._post('10.0.0.2').status_code, 200)
        self.assertEqual(self._post('10.0.0.2').status_code, 429)

    @override_settings(RATE_LIMIT_CHECKOUT=(1, 60))
    def test_get_is_never_limited_and_does_not_consume_quota(self):
        for _ in range(5):
            response = _checkout_view(
                self.factory.get('/', REMOTE_ADDR=self.ip)
            )
            self.assertEqual(response.status_code, 200)
        self.assertIsNone(cache.get(f'rate:checkout:{self.ip}'))
        # The POST quota is still intact.
        self.assertEqual(self._post().status_code, 200)

    @override_settings(RATE_LIMIT_COUPON=(1, 60))
    def test_405_does_not_consume_quota(self):
        for _ in range(5):
            response = _post_only_view(
                self.factory.put('/', REMOTE_ADDR=self.ip)
            )
            self.assertEqual(response.status_code, 405)
        self.assertIsNone(cache.get(f'rate:coupon:{self.ip}'))

        self.assertEqual(
            _post_only_view(self.factory.post('/', REMOTE_ADDR=self.ip)).status_code,
            200,
        )

    @override_settings(RATE_LIMIT_CHECKOUT=(1, 60))
    def test_window_expiry_resets_the_counter(self):
        before = time.time()
        self.assertEqual(self._post().status_code, 200)
        after = time.time()
        self.assertEqual(self._post().status_code, 429)

        key = f'rate:checkout:{self.ip}'
        deadline = cache.get(f'{key}:window')
        # The window deadline lives in the cache with the window as its
        # timeout, so it is bounded by the configured period.
        self.assertGreaterEqual(deadline, before + 60)
        self.assertLessEqual(deadline, after + 60)

        # Simulate the window rolling over (both keys expire together).
        cache.delete_many([key, f'{key}:window'])
        self.assertEqual(self._post().status_code, 200)

    @override_settings(RATE_LIMIT_CHECKOUT=(1, 0))
    def test_zero_period_disables_limiting(self):
        for _ in range(3):
            self.assertEqual(self._post().status_code, 200)


@override_settings(CACHES=LOCMEM_CACHES)
class ClientIpTests(SimpleTestCase):
    """Which address selects the bucket, and when X-Forwarded-For is trusted."""

    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()
        self.remote_addr = '172.18.0.5'

    def _request(self, forwarded_for=None):
        extra = {'REMOTE_ADDR': self.remote_addr}
        if forwarded_for is not None:
            extra['HTTP_X_FORWARDED_FOR'] = forwarded_for
        return self.factory.post('/', **extra)

    @override_settings(RATE_LIMIT_TRUST_X_FORWARDED_FOR=False)
    def test_untrusted_x_forwarded_for_is_ignored(self):
        self.assertEqual(client_ip(self._request('8.8.8.8')), self.remote_addr)

        with override_settings(RATE_LIMIT_CHECKOUT=(1, 60)):
            self.assertEqual(_checkout_view(self._request('8.8.8.8')).status_code, 200)
            # Same REMOTE_ADDR, different forged hop → same bucket → limited.
            self.assertEqual(_checkout_view(self._request('9.9.9.9')).status_code, 429)
        self.assertIsNone(cache.get('rate:checkout:8.8.8.8'))

    @override_settings(RATE_LIMIT_TRUST_X_FORWARDED_FOR=True)
    def test_trusted_x_forwarded_for_first_public_hop_decides_bucket(self):
        self.assertEqual(client_ip(self._request('8.8.8.8')), '8.8.8.8')

        with override_settings(RATE_LIMIT_CHECKOUT=(1, 60)):
            self.assertEqual(_checkout_view(self._request('8.8.8.8')).status_code, 200)
            # Same forwarded client, different (shared) proxy address → limited.
            self.assertEqual(_checkout_view(self._request('8.8.8.8')).status_code, 429)
            # Another forwarded client behind the same proxy gets its own bucket.
            self.assertEqual(_checkout_view(self._request('9.9.9.9')).status_code, 200)
        self.assertEqual(cache.get('rate:checkout:8.8.8.8'), 2)

    @override_settings(RATE_LIMIT_TRUST_X_FORWARDED_FOR=True)
    def test_private_hops_are_skipped(self):
        request = self._request('10.0.0.7, 8.8.8.8, 172.18.0.5')
        self.assertEqual(client_ip(request), '8.8.8.8')

    @override_settings(RATE_LIMIT_TRUST_X_FORWARDED_FOR=True)
    def test_forwarded_for_without_public_hop_falls_back_to_remote_addr(self):
        self.assertEqual(
            client_ip(self._request('10.0.0.7, 192.168.1.9')),
            self.remote_addr,
        )
        self.assertEqual(client_ip(self._request('not-an-ip')), self.remote_addr)
        self.assertEqual(client_ip(self._request('')), self.remote_addr)


@override_settings(CACHES=LOCMEM_CACHES)
class CouponApplyRateLimitTests(TestCase):
    """End-to-end through the URL → view → decorator stack."""

    def setUp(self):
        cache.clear()
        self.url = reverse('coupons:apply')

    @override_settings(RATE_LIMIT_COUPON=(2, 60))
    def test_third_post_is_429_and_cache_clear_recovers(self):
        cart_url = reverse('cart:cart_detail')
        for _ in range(2):
            response = self.client.post(self.url, {'code': 'DOES-NOT-EXIST'})
            self.assertRedirects(response, cart_url, fetch_redirect_response=False)

        response = self.client.post(self.url, {'code': 'DOES-NOT-EXIST'})
        self.assertEqual(response.status_code, 429)
        self.assertGreaterEqual(int(response['Retry-After']), 1)

        cache.clear()
        response = self.client.post(self.url, {'code': 'DOES-NOT-EXIST'})
        self.assertRedirects(response, cart_url, fetch_redirect_response=False)

    @override_settings(RATE_LIMIT_COUPON=(1, 60))
    def test_get_is_405_and_does_not_consume_quota(self):
        for _ in range(3):
            self.assertEqual(self.client.get(self.url).status_code, 405)
        self.assertEqual(
            self.client.post(self.url, {'code': 'DOES-NOT-EXIST'}).status_code,
            302,
        )


@override_settings(CACHES=LOCMEM_CACHES)
class CheckoutRateLimitTests(TestCase):
    """The checkout form stays readable while its POSTs are limited."""

    def setUp(self):
        cache.clear()
        self.url = reverse('orders:order_create')

    @override_settings(RATE_LIMIT_CHECKOUT=(1, 60))
    def test_gets_are_unlimited(self):
        for _ in range(3):
            self.assertEqual(self.client.get(self.url).status_code, 200)

    @override_settings(RATE_LIMIT_CHECKOUT=(1, 60))
    def test_second_post_is_429(self):
        # An invalid (empty) form still counts: the limiter runs before the view.
        self.assertEqual(self.client.post(self.url, {}).status_code, 200)
        response = self.client.post(self.url, {})
        self.assertEqual(response.status_code, 429)
        self.assertGreaterEqual(int(response['Retry-After']), 1)


@override_settings(CACHES=LOCMEM_CACHES)
class TrackingRateLimitTests(TestCase):
    """End-to-end through the URL -> view -> decorator stack (card ee76485f)."""

    def setUp(self):
        cache.clear()
        self.url = reverse('accounts:order_track')

    @override_settings(RATE_LIMIT_TRACKING=(2, 60))
    def test_third_post_is_429_and_cache_clear_recovers(self):
        for _ in range(2):
            response = self.client.post(
                self.url, {'order_id': '1', 'email': 'a@b.com'},
            )
            self.assertEqual(response.status_code, 200)

        response = self.client.post(
            self.url, {'order_id': '1', 'email': 'a@b.com'},
        )
        self.assertEqual(response.status_code, 429)
        self.assertGreaterEqual(int(response['Retry-After']), 1)

        cache.clear()
        response = self.client.post(
            self.url, {'order_id': '1', 'email': 'a@b.com'},
        )
        self.assertEqual(response.status_code, 200)

