"""Test runner that keeps the test suite off the shared cache.

Card ef86d264 — the POST rate-limit counters live in the shared Redis
instance (DB 2) and survive between runs, so a suite running against that
cache would depend on how often it was started: after a few runs the
checkout/coupon limits trip and unrelated tests fail with ``429``.

The suite therefore always runs on the per-process LocMem cache, no matter
how it is started (``manage.py test``, ``make test``, ``docker compose run``).
"""
from django.test.runner import DiscoverRunner
from django.test.utils import override_settings

TEST_CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'ecommerce-tests',
    }
}


class LocMemCacheTestRunner(DiscoverRunner):
    """``DiscoverRunner`` that swaps the cache for a LocMem cache."""

    def run_tests(self, test_labels, **kwargs):
        with override_settings(CACHES=TEST_CACHES):
            return super().run_tests(test_labels, **kwargs)
