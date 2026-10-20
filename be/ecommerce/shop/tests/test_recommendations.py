"""Product recommendations (card 45ba5cfh).

Rule-based "frequently bought together" / "you might also like" helper:
same-category products whose price is within a configurable band of the
current product's price, excluding the current product and any unavailable
products.  The helper is a pure function over the ORM (no Redis, no Stripe)
so it is safe for anonymous visitors and deterministic in tests.

Covers:
* Selection: same category, price range, excludes self.
* Unavailable products are excluded.
* Empty / small catalogs return [].
* Price-range and category boundary behavior.
* Default limit (4) and explicit limit.
* Deterministic ordering (by price distance, then name).
* View + template integration on the product detail page.
* Query efficiency (bounded query count on detail).
"""
from decimal import Decimal

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from shop.models import Category, Product
from shop.recommendations import recommend_products


class RecommendationFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.home = Category.objects.create(name='Home', slug='home')
        cls.garden = Category.objects.create(name='Garden', slug='garden')

        # Home category — prices around $10
        cls.mug = Product.objects.create(
            category=cls.home, name='Mug', slug='mug',
            price=Decimal('10.00'), available=True,
        )
        cls.bowl = Product.objects.create(
            category=cls.home, name='Bowl', slug='bowl',
            price=Decimal('12.00'), available=True,
        )
        cls.plate = Product.objects.create(
            category=cls.home, name='Plate', slug='plate',
            price=Decimal('15.00'), available=True,
        )
        # Out of range (too expensive)
        cls.expensive = Product.objects.create(
            category=cls.home, name='Expensive Vase', slug='expensive-vase',
            price=Decimal('100.00'), available=True,
        )
        # Unavailable — should never appear even in range
        cls.unavailable = Product.objects.create(
            category=cls.home, name='Gone Mug', slug='gone-mug',
            price=Decimal('11.00'), available=False,
        )
        # Different category — excluded
        cls.garden_tool = Product.objects.create(
            category=cls.garden, name='Trowel', slug='trowel',
            price=Decimal('12.00'), available=True,
        )

    def setUp(self):
        super().setUp()
        # Clear cache between tests (catalog cache invalidation card).
        from django.core.cache import cache as django_cache
        django_cache.clear()


# ---------------------------------------------------------------------------
# Service-level tests
# ---------------------------------------------------------------------------


class RecommendProductsTests(RecommendationFixture):
    """Tests for the standalone ``recommend_products`` helper."""

    def test_excludes_the_current_product(self):
        results = recommend_products(self.mug)
        self.assertNotIn(self.mug, results)
        self.assertNotIn(self.mug.id, [p.id for p in results])

    def test_only_same_category(self):
        results = recommend_products(self.mug)
        categories = {p.category for p in results}
        self.assertEqual(categories, {self.home})
        self.assertNotIn(self.garden_tool, results)

    def test_returns_products_within_price_range(self):
        # mug is $10, band is +/- 50% → $5 .. $15
        results = recommend_products(self.mug)
        ids = {p.id for p in results}
        self.assertIn(self.bowl.id, ids)      # $12 → in range
        self.assertIn(self.plate.id, ids)     # $15 → in range (boundary)
        self.assertNotIn(self.expensive.id, ids)  # $100 → out of range

    def test_excludes_unavailable_products(self):
        results = recommend_products(self.mug)
        ids = [p.id for p in results]
        self.assertNotIn(self.unavailable.id, ids)

    def test_excludes_unavailable_requested_product(self):
        """If the requested product itself is unavailable, still returns []
        (it is excluded, and the queryset itself filters available=True)."""
        self.unavailable.available = False
        self.unavailable.save()
        self.assertEqual(list(recommend_products(self.unavailable)), [])

    def test_empty_catalog_returns_empty(self):
        Product.objects.all().delete()
        self.assertEqual(list(recommend_products(self.mug)), [])

    def test_single_product_catalog_returns_empty(self):
        """Only the current product exists — nothing to recommend."""
        Product.objects.filter(pk__in=[
            self.bowl.pk, self.plate.pk, self.expensive.pk,
            self.unavailable.pk, self.garden_tool.pk,
        ]).delete()
        self.assertEqual(list(recommend_products(self.mug)), [])

    def test_default_limit_is_four(self):
        # Create enough in-range products to exceed the default limit of 4.
        for i in range(6):
            Product.objects.create(
                category=self.home,
                name=f'Extra{i}',
                slug=f'extra{i}',
                price=Decimal('10.00'),
                available=True,
            )
        results = recommend_products(self.mug)
        self.assertEqual(len(results), 4)

    def test_explicit_limit_respected(self):
        # bowl ($12) and plate ($15) are both in range of mug ($10)
        results = recommend_products(self.mug, limit=1)
        self.assertEqual(len(results), 1)

    def test_explicit_limit_larger_than_results(self):
        results = recommend_products(self.mug, limit=10)
        # bowl ($12), plate ($15) are in range; expensive ($100) is not.
        self.assertEqual(len(results), 2)

    def test_deterministic_ordering_by_price_distance(self):
        # Add a product exactly at the midpoint of the band.
        tie = Product.objects.create(
            category=self.home, name='Tie', slug='tie',
            price=Decimal('12.50'), available=True,
        )
        results = recommend_products(self.mug)
        # bowl ($12, distance 2), tie ($12.50, distance 2.5), plate ($15, dist 5)
        prices = [str(p.price) for p in results]
        self.assertEqual(prices, ['12.00', '12.50', '15.00'])

    def test_ties_broken_by_name_asc(self):
        # Two products at the same distance from mug ($10): $8 and $12.
        # Same distance → alphabetical by name (Bowl < Cheap).
        cheap = Product.objects.create(
            category=self.home, name='Cheap', slug='cheap',
            price=Decimal('8.00'), available=True,
        )
        results = recommend_products(self.mug)
        prices = [(p.name, str(p.price)) for p in results]
        # bowl ($12, dist 2) comes before cheap ($8, dist 2) — 'B' < 'C'
        self.assertEqual(prices[0], ('Bowl', '12.00'))
        self.assertIn(('Cheap', '8.00'), prices)

    def test_zero_price_product_recommended_from(self):
        """A product with price 0 should still work: band is [0, 0]."""
        free = Product.objects.create(
            category=self.home, name='Freebie', slug='freebie',
            price=Decimal('0.00'), available=True,
        )
        other_free = Product.objects.create(
            category=self.home, name='Other Free', slug='other-free',
            price=Decimal('0.00'), available=True,
        )
        results = recommend_products(free)
        self.assertIn(other_free, results)

    def test_returns_queryset_not_list(self):
        from django.db.models.query import QuerySet
        qs = recommend_products(self.mug)
        self.assertIsInstance(qs, QuerySet)
        self.assertEqual(list(qs), list(qs))  # re-evaluable

    def test_recommendations_are_fresh_queryset(self):
        """Adding a new matching product is reflected on the next call."""
        results_before = recommend_products(self.mug)
        before_count = len(results_before)
        Product.objects.create(
            category=self.home, name='New Item', slug='new-item',
            price=Decimal('10.50'), available=True,
        )
        results_after = recommend_products(self.mug)
        after_count = len(results_after)
        self.assertEqual(after_count, before_count + 1,
                         f'before={before_count}, after={after_count}')


# ---------------------------------------------------------------------------
# View + template integration tests
# ---------------------------------------------------------------------------


class ProductDetailRecommendationTests(RecommendationFixture):
    """The product detail page must render recommendations."""

    def test_detail_page_renders_recommendations_section(self):
        response = self.client.get(self.mug.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('recommendations', html.lower())
        self.assertIn('You might also like', html)

    def test_detail_page_lists_recommended_products(self):
        response = self.client.get(self.mug.get_absolute_url())
        html = response.content.decode()
        # bowl and plate are in range of mug
        self.assertIn('Bowl', html)
        self.assertIn('Plate', html)

    def test_detail_page_excludes_current_product(self):
        response = self.client.get(self.mug.get_absolute_url())
        html = response.content.decode()
        # The recommendation section is between "You might also like" and the
        # reviews section. The current product's detail URL should not appear
        # as a recommendation link in that section.
        detail_url = reverse('shop:product_detail', args=[self.mug.id, self.mug.slug])
        recommendation_section = html.split('You might also like')[1].split('product-reviews')[0]
        self.assertNotIn(detail_url, recommendation_section)

    def test_detail_page_excludes_unavailable(self):
        response = self.client.get(self.mug.get_absolute_url())
        html = response.content.decode()
        self.assertNotIn('Gone Mug', html)
        self.assertNotIn('Expensive Vase', html)

    def test_detail_page_no_recommendations_message(self):
        """When there are no recommendations, show a fallback message."""
        Product.objects.filter(
            pk__in=[self.bowl.pk, self.plate.pk, self.expensive.pk],
        ).update(available=False)
        response = self.client.get(self.mug.get_absolute_url())
        html = response.content.decode()
        self.assertIn('You might also like', html)
        self.assertIn('No recommendations available', html)

    def test_recommendations_safe_for_anonymous(self):
        """Anonymous visitors get the same deterministic recommendations."""
        response = self.client.get(self.mug.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('Bowl', html)


# ---------------------------------------------------------------------------
# Query efficiency test
# ---------------------------------------------------------------------------


class ProductDetailRecommendationQueryCountTests(RecommendationFixture):
    """detail must add only a bounded number of queries for recommendations."""

    def test_detail_query_count_with_recommendations(self):
        # Seed enough recommended products to make N+1 obvious if it happens.
        for i in range(10):
            Product.objects.create(
                category=self.home,
                name=f'Bulk{i:02d}',
                slug=f'bulk{i:02d}',
                price=Decimal('10.00'),
                available=True,
            )
        url = self.mug.get_absolute_url()
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        sqls = [q['sql'] for q in ctx.captured_queries]
        # product+category (1), variants prefetch (1), reviews prefetch (1),
        # review-user prefetch (1), recommendations queryset (1), session
        # overhead (a few).  No per-product query in the template loop.
        self.assertLessEqual(
            len(ctx.captured_queries),
            12,
            'product_detail issued too many queries: ' + str(sqls),
        )

    def test_detail_query_count_does_not_grow_with_recommendations(self):
        """Adding more recommended products must not increase query count."""
        url = self.mug.get_absolute_url()
        with CaptureQueriesContext(connection) as baseline:
            self.client.get(url)

        for i in range(8):
            Product.objects.create(
                category=self.home,
                name=f'Scale{i:02d}',
                slug=f'scale{i:02d}',
                price=Decimal('10.00'),
                available=True,
            )
        with CaptureQueriesContext(connection) as after:
            self.client.get(url)

        self.assertEqual(
            len(after.captured_queries),
            len(baseline.captured_queries),
            'query count grew with recommendation count — N+1 regression: '
            + str([q['sql'] for q in after.captured_queries]),
        )
