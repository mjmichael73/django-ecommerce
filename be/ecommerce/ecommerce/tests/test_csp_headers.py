"""Content Security Policy header tests (card f3d54f76).

Covers the middleware unit (RequestFactory), end-to-end header presence
through the test Client (catalog home, admin login, and the 429 coupon path),
and the CSP_POLICY settings assembly.

The shared Redis cache is never touched: TEST_RUNNER forces LocMem and the
rate-limit counter is cleared in setUp.
"""
from django.http import HttpResponse
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from ecommerce.csp_middleware import CSPMiddleware
from ecommerce.settings import CSP_POLICY

# Compiled policy the middleware must emit when CSP_REPORT_URI is unset.
# Asserting a stable literal string (rather than rebuilding it from settings)
# catches accidental policy drift.
EXPECTED_POLICY = (
    "default-src 'self'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "object-src 'none'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self'"
)

LOCMEM_CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'csp-header-tests',
    }
}


def _dummy_view(request):
    return HttpResponse('ok')


class CSPMiddlewareUnitTests(SimpleTestCase):
    """The middleware emits exactly the compiled policy, no per-request parsing."""

    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = CSPMiddleware(_dummy_view)

    def test_response_carries_exact_policy_header(self):
        request = self.factory.get('/')
        response = self.middleware(request)
        self.assertEqual(response['Content-Security-Policy'], EXPECTED_POLICY)

    def test_header_is_present_on_every_method_and_path(self):
        for path in ('/', '/cart/', '/orders/create/'):
            request = self.factory.get(path)
            response = self.middleware(request)
            self.assertEqual(response['Content-Security-Policy'], EXPECTED_POLICY)

    def test_no_report_uri_by_default(self):
        """CSP_REPORT_URI is empty in the test image, so no report-uri directive."""
        self.assertNotIn('report-uri', EXPECTED_POLICY)
        response = self.middleware(self.factory.get('/'))
        self.assertNotIn('report-uri', response['Content-Security-Policy'])

    def test_script_src_has_no_unsafe_inline(self):
        """script-src stays 'self': the only inline handler (admin order
        detail ``onclick``) was moved to shop/static/js/admin-order-print.js,
        so nothing forces 'unsafe-inline' into script-src."""
        segments = dict(seg.split(' ', 1) for seg in EXPECTED_POLICY.split('; '))
        self.assertEqual(segments['script-src'], "'self'")
        self.assertNotIn("'unsafe-inline'", segments['script-src'])


class CSPPolicySettingsTests(SimpleTestCase):
    """CSP_POLICY assembles exactly the documented directive set."""

    def test_csp_policy_directives(self):
        self.assertEqual(
            CSP_POLICY,
            {
                "default-src": "'self'",
                "base-uri": "'self'",
                "form-action": "'self'",
                "frame-ancestors": "'none'",
                "object-src": "'none'",
                "script-src": "'self'",
                "style-src": "'self' 'unsafe-inline'",
                "img-src": "'self'",
            },
        )


@override_settings(CACHES=LOCMEM_CACHES)
class CSPClientIntegrationTests(TestCase):
    """Header reaches real responses through the middleware stack."""

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.client = Client()

    def test_catalog_home_carries_the_header(self):
        response = self.client.get(reverse('shop:product_list'))
        self.assertEqual(response['Content-Security-Policy'], EXPECTED_POLICY)

    def test_admin_login_carries_the_identical_header(self):
        response = self.client.get(reverse('admin:login'))
        self.assertEqual(response['Content-Security-Policy'], EXPECTED_POLICY)

    @override_settings(RATE_LIMIT_COUPON=(1, 60))
    def test_coupon_apply_429_carries_the_header(self):
        """The 429 path (exercised by T1's rate-limit tests) also gets the header."""
        url = reverse('coupons:apply')
        self.client.post(url, {'code': 'DOES-NOT-EXIST'})  # consumes the single allowance
        response = self.client.post(url, {'code': 'DOES-NOT-EXIST'})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response['Content-Security-Policy'], EXPECTED_POLICY)
