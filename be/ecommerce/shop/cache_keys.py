"""Cache key helpers for the product catalog (card 29b8eadg).

Deterministic, self-describing keys derived from the request parameters so
every distinct page/filter combination gets its own entry:

- ``catalog:products:{hash}``   — paginated product list + paginator metadata
- ``catalog:facets:{hash}``     — facet counts dict (same hash as the listing)
- ``catalog:categories``         — global category list (longer TTL)

The hash is ``md5`` over a stable string built from the request params
(category_slug, query, sorted facet selection, page). Using a hash keeps keys
short and avoids issues with complex query strings.
"""
from __future__ import annotations

import hashlib
import json

from shop.facets import PARAM_PAGE

# TTLs (seconds).
PRODUCTS_TTL = 300   # 5 minutes — listings are volatile (stock, prices, reviews)
CATEGORIES_TTL = 600  # 10 minutes — categories change less often

# Prefixes (also used by cache.delete_many via the ``keys_with_prefix`` helper).
PRODUCTS_PREFIX = 'catalog:products:'
FACETS_PREFIX = 'catalog:facets:'
CATEGORIES_KEY = 'catalog:categories'

# Registry key: holds the set of currently-active listing/facet hashes so
# invalidation can enumerate them for ``delete_many`` (neither LocMem nor
# Redis cache backends expose prefix-based deletion natively).
_REGISTRY_KEY = 'catalog:registry'


def _stable_params(category_slug, query, selection, page):
    """Build the deterministic parameter signature for the current request.

    ``category_slug`` is ``None`` or a slug string; ``query`` is the trimmed
    search term; ``selection`` is a :class:`~shop.facets.FacetSelection`;
    ``page`` is the page number (1-based int or the string from the request).
    """
    return {
        'category': category_slug or '',
        'query': query or '',
        'categories': sorted(selection.categories),
        'prices': sorted(selection.prices),
        'rating': selection.rating,
        'stock': selection.stock,
        'page': str(page),
    }


def _params_hash(category_slug, query, selection, page):
    """Return an md5 hex digest of the stable params dict (deterministic)."""
    params = _stable_params(category_slug, query, selection, page)
    canonical = json.dumps(params, sort_keys=True, default=str)
    return hashlib.md5(canonical.encode('utf-8')).hexdigest()


def listing_params_hash(category_slug, query, selection, page):
    """Public helper: compute the md5 hash for a listing/facet cache key.

    Exposed so tests can assert key determinism without re-implementing the
    hashing logic.
    """
    return _params_hash(category_slug, query, selection, page)


def cache_key_products(category_slug, query, selection, page):
    """Full cache key for the paginated product listing."""
    h = _params_hash(category_slug, query, selection, page)
    return f'{PRODUCTS_PREFIX}{h}'


def cache_key_facets(category_slug, query, selection, page):
    """Full cache key for the facet counts dict (same hash as the listing)."""
    h = _params_hash(category_slug, query, selection, page)
    return f'{FACETS_PREFIX}{h}'


def cache_key_categories():
    """Cache key for the global category list."""
    return CATEGORIES_KEY


def register_cache_key(key):
    """Add *key* to the registry set so it can be bulk-deleted on invalidation.

    Uses a Redis ``SADD``-style set stored under ``_REGISTRY_KEY``. Each
    listing/facet key is registered when first written so that the signal
    handlers can ``delete_many`` the full set on any model change.
    """
    from django.core.cache import cache

    # LocMem/Redis: store a set of keys. We use a list-backed approach that
    # works with any backend: read the list, append, write back.
    keys = cache.get(_REGISTRY_KEY) or []
    if key not in keys:
        keys.append(key)
        cache.set(_REGISTRY_KEY, keys, timeout=None)


def clear_catalog_caches():
    """Invalidate *all* catalog caches (listings, facets, categories).

    Called by post_save/post_delete signals on Product, Category,
    ProductVariant, and ProductReview.
    """
    from django.core.cache import cache

    # Collect all registered listing/facet keys.
    registered = cache.get(_REGISTRY_KEY) or []
    keys_to_delete = list(registered) + [CATEGORIES_KEY]
    cache.delete_many(keys_to_delete)

    # Clear the registry itself so it doesn't grow unbounded.
    cache.delete(_REGISTRY_KEY)
