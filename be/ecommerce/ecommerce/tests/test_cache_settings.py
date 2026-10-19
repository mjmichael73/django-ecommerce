"""Tests for the cache selection helper (card ef86d264).

The shared Redis instance (DB 2) holds the POST rate-limit counters. Tests
must never read or mutate it, so an unset **or empty** ``REDIS_CACHE_URL``
has to select the LocMem cache.
"""
from django.core.cache import caches
from django.test import SimpleTestCase

from ecommerce.cache_config import cache_config

LOCMEM_BACKEND = 'django.core.cache.backends.locmem.LocMemCache'
REDIS_BACKEND = 'django.core.cache.backends.redis.RedisCache'


class CacheConfigTests(SimpleTestCase):
    def test_unset_redis_url_uses_locmem_cache(self):
        config = cache_config({})
        self.assertEqual(config['BACKEND'], LOCMEM_BACKEND)

    def test_empty_redis_url_uses_locmem_cache(self):
        config = cache_config({'REDIS_CACHE_URL': ''})
        self.assertEqual(config['BACKEND'], LOCMEM_BACKEND)

    def test_redis_url_uses_redis_cache_with_that_location(self):
        config = cache_config({'REDIS_CACHE_URL': 'redis://redis:6379/2'})
        self.assertEqual(config['BACKEND'], REDIS_BACKEND)
        self.assertEqual(config['LOCATION'], 'redis://redis:6379/2')


class TestRunnerCacheTests(SimpleTestCase):
    """The suite itself must never run against the shared Redis cache."""

    def test_suite_runs_on_the_locmem_cache(self):
        self.assertEqual(
            caches['default'].__class__.__module__,
            'django.core.cache.backends.locmem',
        )
