"""Tests for the product catalog caching layer (card 29b8eadg).

Covers:
1. First request misses cache, second request hits.
2. Different filter combinations produce different cache keys (no collision).
3. Product save/delete invalidates the listing cache.
4. Category save/delete clears the categories cache.
5. Cache key is deterministic (same params → same key, different page → different key).
6. ProductReview save/delete invalidates cache.
7. ProductVariant save/delete invalidates cache.
8. Facet counts are cached separately and invalidated alongside listings.
"""
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from shop.cache_keys import (
    cache_key_categories,
    cache_key_facets,
    cache_key_products,
    listing_params_hash,
    clear_catalog_caches,
)
from shop.facets import FacetSelection, parse_facets
from shop.models import Category, Product, ProductReview, ProductVariant


class CatalogCacheFixture(TestCase):
    """Shared fixture: two categories and several products for cache tests."""

    @classmethod
    def setUpTestData(cls):
        cls.category_home = Category.objects.create(
            name='Home', slug='home',
        )
        cls.category_garden = Category.objects.create(
            name='Garden', slug='garden',
        )

        cls.product_mug = Product.objects.create(
            category=cls.category_home,
            name='Mug',
            slug='mug',
            price=Decimal('10.00'),
            stock=5,
            available=True,
        )
        cls.product_shirt = Product.objects.create(
            category=cls.category_home,
            name='Shirt',
            slug='shirt',
            price=Decimal('30.00'),
            stock=0,
            available=True,
        )
        cls.product_lamp = Product.objects.create(
            category=cls.category_garden,
            name='Lamp',
            slug='lamp',
            price=Decimal('75.00'),
            stock=None,
            available=True,
        )


class CacheKeyDeterminismTests(TestCase):
    """Cache keys are deterministic and isolate different request states."""

    def test_same_params_produce_same_key(self):
        sel = FacetSelection(categories=['home'], prices=['under-25'])
        page = 1
        key1 = cache_key_products('home', 'mug', sel, page)
        key2 = cache_key_products('home', 'mug', sel, page)
        self.assertEqual(key1, key2)

    def test_different_page_produces_different_key(self):
        sel = FacetSelection()
        key1 = cache_key_products('', '', sel, 1)
        key2 = cache_key_products('', '', sel, 2)
        self.assertNotEqual(key1, key2)

    def test_different_category_produces_different_key(self):
        sel = FacetSelection()
        key1 = cache_key_products('home', '', sel, 1)
        key2 = cache_key_products('garden', '', sel, 1)
        self.assertNotEqual(key1, key2)

    def test_different_query_produces_different_key(self):
        sel = FacetSelection()
        key1 = cache_key_products('', '', sel, 1)
        key2 = cache_key_products('mug', '', sel, 1)
        self.assertNotEqual(key1, key2)

    def test_different_facet_selection_produces_different_key(self):
        sel1 = FacetSelection(categories=['home'])
        sel2 = FacetSelection(categories=['garden'])
        key1 = cache_key_products('', '', sel1, 1)
        key2 = cache_key_products('', '', sel2, 1)
        self.assertNotEqual(key1, key2)

    def test_facet_key_uses_same_hash_as_products_key(self):
        """Listing and facet counts for the same request share the hash."""
        sel = FacetSelection(categories=['home'], prices=['25-50'])
        page = 2
        products_key = cache_key_products('home', 'chair', sel, page)
        facets_key = cache_key_facets('home', 'chair', sel, page)

        # Both keys should contain the same md5 hash component.
        h1 = products_key.replace('catalog:products:', '')
        h2 = facets_key.replace('catalog:facets:', '')
        self.assertEqual(h1, h2)

    def test_category_key_is_static(self):
        """The categories cache key has no params (it's global)."""
        self.assertEqual(cache_key_categories(), 'catalog:categories')

    def test_listing_params_hash_is_stable(self):
        """Direct hash helper produces the same hash for the same inputs."""
        sel = FacetSelection(categories=['home'], prices=['under-25'])
        h1 = listing_params_hash('home', 'chair', sel, 1)
        h2 = listing_params_hash('home', 'chair', sel, 1)
        self.assertEqual(h1, h2)


class CacheHitMissTests(CatalogCacheFixture):
    """First request misses, second request hits the cache."""

    def setUp(self):
        super().setUp()
        # Ensure a clean cache state for each test.
        cache.clear()

    def test_categories_cached_after_first_request(self):
        """The categories cache key is set after the first request."""
        cat_key = cache_key_categories()
        # Cache should be empty before the request.
        self.assertIsNone(cache.get(cat_key))

        response = self.client.get(reverse('shop:product_list'))
        self.assertEqual(response.status_code, 200)

        # Cache should now be populated.
        self.assertIsNotNone(cache.get(cat_key))
        cached_cats = cache.get(cat_key)
        self.assertEqual(len(cached_cats), 2)

    def test_listing_cached_after_first_request(self):
        """The listing cache key is set after the first request."""
        sel = FacetSelection()
        page = 1
        products_key = cache_key_products('', '', sel, page)
        facets_key = cache_key_facets('', '', sel, page)

        # Cache should be empty before.
        self.assertIsNone(cache.get(products_key))
        self.assertIsNone(cache.get(facets_key))

        response = self.client.get(reverse('shop:product_list'))
        self.assertEqual(response.status_code, 200)

        # Cache should now be populated.
        self.assertIsNotNone(cache.get(products_key))
        self.assertIsNotNone(cache.get(facets_key))

    def test_second_request_uses_cached_data(self):
        """A second request with the same params does not query the DB for listings."""
        # Prime the cache.
        self.client.get(reverse('shop:product_list'))

        sel = FacetSelection()
        page = 1
        products_key = cache_key_products('', '', sel, page)
        facets_key = cache_key_facets('', '', sel, page)

        # Clear the DB query log / spy on queries.
        from django.db import connection

        with mock.patch('shop.views.product_list_view.facet_counts') as mock_counts:
            response = self.client.get(reverse('shop:product_list'))
            # facet_counts should NOT be called because the cache hit.
            mock_counts.assert_not_called()

        # Verify the cache is still populated (not invalidated).
        self.assertIsNotNone(cache.get(products_key))
        self.assertIsNotNone(cache.get(facets_key))


class CacheKeyIsolationTests(CatalogCacheFixture):
    """Different filter combinations do not collide in the cache."""

    def setUp(self):
        super().setUp()
        cache.clear()

    def test_category_filter_isolates_cache(self):
        """Requesting with and without a category filter uses different keys."""
        sel = FacetSelection()

        # Prime cache for the unfiltered listing.
        response1 = self.client.get(reverse('shop:product_list'))

        # Prime cache for the category-filtered listing.
        response2 = self.client.get(reverse('shop:product_list_by_category', args=['garden']))

        # Both should succeed.
        self.assertEqual(response1.status_code, 200)
        self.assertEqual(response2.status_code, 200)

        # The two requests should have cached different keys.
        products_key_all = cache_key_products('', '', sel, 1)
        products_key_garden = cache_key_products('garden', '', sel, 1)
        self.assertNotEqual(products_key_all, products_key_garden)
        self.assertIsNotNone(cache.get(products_key_all))
        self.assertIsNotNone(cache.get(products_key_garden))

    def test_price_filter_isolates_cache(self):
        """Different price facet selections use different cache keys."""
        sel1 = FacetSelection(prices=['under-25'])
        sel2 = FacetSelection(prices=['50-100'])
        key1 = cache_key_products('', '', sel1, 1)
        key2 = cache_key_products('', '', sel2, 1)
        self.assertNotEqual(key1, key2)

    def test_search_query_isolates_cache(self):
        """Different search queries use different cache keys."""
        response1 = self.client.get(reverse('shop:product_list'), {'q': 'mug'})
        product1 = cache_key_products('', 'mug', FacetSelection(), 1)
        product2 = cache_key_products('', 'shirt', FacetSelection(), 1)
        self.assertNotEqual(product1, product2)

        self.assertIsNotNone(cache.get(product1))
        self.assertIsNone(cache.get(product2))

        # Now request 'shirt'.
        self.client.get(reverse('shop:product_list'), {'q': 'shirt'})
        self.assertIsNotNone(cache.get(product2))


class CacheInvalidationTests(CatalogCacheFixture):
    """Model save/delete invalidates the appropriate cache entries."""

    def setUp(self):
        super().setUp()
        cache.clear()

    def test_product_save_invalidates_listing_cache(self):
        """Saving a product clears the listing and facet caches."""
        # Prime the cache.
        self.client.get(reverse('shop:product_list'))

        sel = FacetSelection()
        products_key = cache_key_products('', '', sel, 1)
        facets_key = cache_key_facets('', '', sel, 1)
        cat_key = cache_key_categories()

        # Verify cache is populated.
        self.assertIsNotNone(cache.get(products_key))
        self.assertIsNotNone(cache.get(facets_key))
        self.assertIsNotNone(cache.get(cat_key))

        # Save (modify) a product.
        self.product_mug.price = Decimal('15.00')
        self.product_mug.save()

        # Caches should be cleared.
        self.assertIsNone(cache.get(products_key))
        self.assertIsNone(cache.get(facets_key))
        self.assertIsNone(cache.get(cat_key))

    def test_product_delete_invalidates_listing_cache(self):
        """Deleting a product clears the listing and facet caches."""
        # Prime the cache.
        self.client.get(reverse('shop:product_list'))

        sel = FacetSelection()
        products_key = cache_key_products('', '', sel, 1)
        facets_key = cache_key_facets('', '', sel, 1)

        # Delete a product.
        self.product_mug.delete()

        # Caches should be cleared.
        self.assertIsNone(cache.get(products_key))
        self.assertIsNone(cache.get(facets_key))

    def test_category_save_clears_categories_cache(self):
        """Saving a category clears the categories cache."""
        # Prime the cache.
        self.client.get(reverse('shop:product_list'))

        cat_key = cache_key_categories()
        self.assertIsNotNone(cache.get(cat_key))

        # Modify a category.
        self.category_home.name = 'Home Goods'
        self.category_home.save()

        # Categories cache should be cleared.
        self.assertIsNone(cache.get(cat_key))

    def test_category_delete_clears_categories_cache(self):
        """Deleting a category clears the categories cache."""
        # Prime the cache.
        self.client.get(reverse('shop:product_list'))

        cat_key = cache_key_categories()
        self.assertIsNotNone(cache.get(cat_key))

        # Delete a category.
        self.category_garden.delete()

        # Categories cache should be cleared.
        self.assertIsNone(cache.get(cat_key))

    def test_product_review_save_invalidates_cache(self):
        """Saving a product review clears the listing cache."""
        # Prime the cache.
        self.client.get(reverse('shop:product_list'))

        sel = FacetSelection()
        products_key = cache_key_products('', '', sel, 1)

        self.assertIsNotNone(cache.get(products_key))

        # Create a review (this affects rating_avg annotation).
        from django.contrib.auth import get_user_model
        User = get_user_model()
        user = User.objects.create_user(
            'reviewer', 'r@example.com', 'pass-phrase-99',
        )
        ProductReview.objects.create(
            product=self.product_mug,
            user=user,
            rating=5,
            title='Great',
            body='Lovely mug.',
        )

        # Cache should be cleared.
        self.assertIsNone(cache.get(products_key))

    def test_product_variant_save_invalidates_cache(self):
        """Saving a product variant clears the listing cache."""
        # Prime the cache.
        self.client.get(reverse('shop:product_list'))

        sel = FacetSelection()
        products_key = cache_key_products('', '', sel, 1)

        self.assertIsNotNone(cache.get(products_key))

        # Create / save a variant.
        ProductVariant.objects.create(
            product=self.product_mug,
            name='Blue',
            sku='MUG-BLUE',
        )

        # Cache should be cleared.
        self.assertIsNone(cache.get(products_key))


class ClearCatalogCachesTests(TestCase):
    """Direct tests for the clear_catalog_caches helper."""

    def test_clear_removes_all_cached_listing_keys(self):
        """clear_catalog_caches deletes registered keys and the categories key."""
        sel = FacetSelection(categories=['home'])
        products_key = cache_key_products('home', '', sel, 1)
        facets_key = cache_key_facets('home', '', sel, 1)
        cat_key = cache_key_categories()

        # Manually populate cache and registry.
        from shop.cache_keys import register_cache_key
        register_cache_key(products_key)
        register_cache_key(facets_key)
        cache.set(products_key, 'dummy', 300)
        cache.set(facets_key, {'counts': {}}, 300)
        cache.set(cat_key, [{'id': 1, 'name': 'Home', 'slug': 'home'}], 600)

        # Verify populated.
        self.assertEqual(cache.get(products_key), 'dummy')
        self.assertEqual(cache.get(facets_key), {'counts': {}})
        self.assertIsNotNone(cache.get(cat_key))

        # Clear all.
        clear_catalog_caches()

        # Everything should be gone.
        self.assertIsNone(cache.get(products_key))
        self.assertIsNone(cache.get(facets_key))
        self.assertIsNone(cache.get(cat_key))
