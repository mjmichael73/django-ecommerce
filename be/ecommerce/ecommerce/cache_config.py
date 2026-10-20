"""Cache configuration helpers.

Card ef86d264 — the shared Redis instance (DB 2) holds the POST rate-limit
counters. Test runs must not read or mutate that shared state: the counters
survive between runs and would make the suite depend on how often it was
started. An unset **or empty** ``REDIS_CACHE_URL`` therefore means "no
Redis" — the LocMem cache is used and every process keeps its own cache.
"""
from __future__ import annotations

from collections.abc import Mapping

LOCMEM_BACKEND = 'django.core.cache.backends.locmem.LocMemCache'
REDIS_BACKEND = 'django.core.cache.backends.redis.RedisCache'


def cache_config(env: Mapping[str, str]) -> dict:
    """Build the Django ``CACHES['default']`` entry from *env*.

    An unset or empty ``REDIS_CACHE_URL`` selects the LocMem cache; a set
    one selects the Redis cache pointing at that URL.
    """
    location = env.get('REDIS_CACHE_URL')
    if not location:
        return {
            'BACKEND': LOCMEM_BACKEND,
            'LOCATION': 'ecommerce',
        }
    return {
        'BACKEND': REDIS_BACKEND,
        'LOCATION': location,
    }
