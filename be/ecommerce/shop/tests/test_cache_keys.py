"""Unit tests for shop cache key helpers (card 29b8eadg).

Covers the internal helpers ``_stable_params``, ``_params_hash``, and
``register_cache_key`` directly — the integration-level tests in
``test_catalog_cache.py`` cover the full request cycle.
"""
from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from shop.cache_keys import (
    _stable_params,
    _params_hash,
    register_cache_key,
    clear_catalog_caches,
    cache_key_products,
    cache_key_facets,
    cache_key_categories,
    listing_params_hash,
)
from shop.facets import FacetSelection


class StableParamsTests(TestCase):
    """``_stable_params`` builds the deterministic parameter dict."""

    def test_none_category_becomes_empty_string(self):
        sel = FacetSelection()
        params = _stable_params(None, '', sel, 1)
        self.assertEqual(params['category'], '')

    def test_none_query_becomes_empty_string(self):
        sel = FacetSelection()
        params = _stable_params('home', None, sel, 1)
        self.assertEqual(params['query'], '')

    def test_selection_categories_are_sorted(self):
        sel = FacetSelection(categories=['garden', 'home'])
        params = _stable_params('home', '', sel, 1)
        self.assertEqual(params['categories'], ['garden', 'home'])

    def test_selection_prices_are_sorted(self):
        sel = FacetSelection(prices=['100-plus', 'under-25'])
        params = _stable_params('', '', sel, 1)
        self.assertEqual(params['prices'], ['100-plus', 'under-25'])

    def test_page_is_stringified(self):
        sel = FacetSelection()
        params = _stable_params('', '', sel, 3)
        self.assertEqual(params['page'], '3')


class ParamsHashTests(TestCase):
    """``_params_hash`` and the public ``listing_params_hash``."""

    def test_hash_is_stable_for_same_inputs(self):
        sel = FacetSelection(categories=['home'], prices=['under-25'])
        h1 = listing_params_hash('home', 'query', sel, 2)
        h2 = listing_params_hash('home', 'query', sel, 2)
        self.assertEqual(h1, h2)

    def test_hash_changes_with_different_page(self):
        sel = FacetSelection()
        h1 = listing_params_hash('', '', sel, 1)
        h2 = listing_params_hash('', '', sel, 2)
        self.assertNotEqual(h1, h2)

    def test_hash_changes_with_different_category(self):
        sel = FacetSelection()
        h1 = listing_params_hash('', '', sel, 1)
        h2 = listing_params_hash('home', '', sel, 1)
        self.assertNotEqual(h1, h2)

    def test_products_and_facets_keys_share_hash(self):
        """The listing and facet keys contain the same hash component."""
        sel = FacetSelection(categories=['home'])
        products_key = cache_key_products('home', 'q', sel, 1)
        facets_key = cache_key_facets('home', 'q', sel, 1)
        # Strip the prefix and compare the hash.
        self.assertEqual(
            products_key.replace('catalog:products:', ''),
            facets_key.replace('catalog:facets:', ''),
        )


class RegisterCacheKeyTests(TestCase):
    """``register_cache_key`` and ``clear_catalog_caches``."""

    def setUp(self):
        cache.clear()

    def test_registered_key_can_be_cleared(self):
        key = cache_key_products('', '', FacetSelection(), 1)
        register_cache_key(key)
        cache.set(key, 'value', 300)
        self.assertIsNotNone(cache.get(key))

        clear_catalog_caches()
        self.assertIsNone(cache.get(key))

    def test_register_does_not_duplicate(self):
        key = cache_key_categories()
        register_cache_key(key)
        register_cache_key(key)
        from shop.cache_keys import _REGISTRY_KEY
        registry = cache.get(_REGISTRY_KEY)
        self.assertEqual(registry, [key])

    def test_clear_removes_categories_key(self):
        cat_key = cache_key_categories()
        cache.set(cat_key, ['cat1'], 600)
        self.assertIsNotNone(cache.get(cat_key))
        clear_catalog_caches()
        self.assertIsNone(cache.get(cat_key))

    def test_clear_empties_registry(self):
        from shop.cache_keys import _REGISTRY_KEY
        key = cache_key_products('', '', FacetSelection(), 1)
        register_cache_key(key)
        self.assertIsNotNone(cache.get(_REGISTRY_KEY))
        clear_catalog_caches()
        self.assertIsNone(cache.get(_REGISTRY_KEY))
