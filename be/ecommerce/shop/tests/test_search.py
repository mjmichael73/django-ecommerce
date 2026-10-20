"""Full-text search (card bf247ef9).

``shop.search.search_products`` — the single entry point behind the catalog
``?q=`` parameter. On PostgreSQL the query hits the trigger-maintained
``Product.search_vector`` tsvector column (weighted name > description) and
results come back ranked; on any other engine the same call degrades to an
``icontains`` OR over name/description. The tests assert the *contract*
(results, availability filter, empty-query passthrough, ranking) so they hold
on both backends; Postgres-only internals (trigger, GIN index) are covered by
the migration-time guard and the live ``make migrate`` path.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from shop.models import Category, Product
from shop.search import search_products


class SearchFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(name='Home', slug='home')
        cls.mug = Product.objects.create(
            category=cls.category, name='Ceramic Mug', slug='ceramic-mug',
            description='A sturdy mug for hot tea.',
            price=Decimal('10.00'),
        )
        cls.teapot = Product.objects.create(
            category=cls.category, name='Cast Iron Teapot', slug='teapot',
            description='Brews tea, holds heat, looks great.',
            price=Decimal('40.00'),
        )
        cls.hidden = Product.objects.create(
            category=cls.category, name='Hidden Tea Set', slug='hidden',
            description='Tea but not for sale.',
            price=Decimal('20.00'), available=False,
        )

    def matching(self, query):
        base = Product.objects.filter(available=True)
        return list(search_products(base, query))


class SearchProductsTests(SearchFixture):
    def test_empty_query_returns_queryset_unchanged(self):
        base = Product.objects.filter(available=True)
        self.assertEqual(list(search_products(base, '')), list(base))
        self.assertEqual(list(search_products(base, '   ')), list(base))

    def test_name_match_found(self):
        self.assertEqual(self.matching('mug'), [self.mug])

    def test_description_match_found(self):
        self.assertEqual(self.matching('sturdy'), [self.mug])

    def test_case_insensitive(self):
        # PostgreSQL stems lexemes; the SQLite fallback lowercases. Both
        # must match 'TEAPOT' against 'Teapot'. (Stemming differences —
        # plural vs. singular — are Postgres-only behavior, asserted in
        # PostgresSearchOnlyTests below.)
        self.assertEqual(self.matching('TEAPOT'), [self.teapot])

    def test_unavailable_products_never_match(self):
        self.assertNotIn(self.hidden, self.matching('tea'))
        self.assertNotIn(self.hidden, self.matching('hidden'))

    def test_multi_term_matches_both_products(self):
        results = self.matching('tea mug')
        self.assertIn(self.mug, results)
        self.assertIn(self.teapot, results)
        self.assertNotIn(self.hidden, results)

    def test_no_match_is_empty(self):
        self.assertEqual(self.matching('zebra'), [])

    def test_stopword_terms_are_dropped_not_universal(self):
        # "or" is an English stopword -> empty tsquery operand, which inside
        # || would match EVERY row. It must be dropped instead; the real
        # terms still match, and nothing unlisted leaks. (On SQLite the
        # fallback has no stopword concept; this still passes because the
        # OR'd icontains just match more loosely — the availability filter
        # still holds.)
        results = self.matching('tea OR mug')
        self.assertIn(self.mug, results)
        self.assertIn(self.teapot, results)
        self.assertNotIn(self.hidden, results)

    def test_mixed_real_and_stopword_terms_still_match(self):
        # 'or' is dropped as a stopword; 'tea' still matches every product
        # whose text contains it.
        results = self.matching('tea or')
        self.assertIn(self.teapot, results)
        self.assertNotIn(self.hidden, results)

    def test_ranking_prefers_name_match(self):
        # 'teapot' appears in both name and description of the teapot but
        # only in the description of the mug's neighbour text — the teapot
        # must rank first. (The hidden tea set is excluded by availability.)
        results = self.matching('teapot')
        self.assertEqual(results[0], self.teapot)


class SearchViewTests(SearchFixture):
    def test_query_parameter_filters_listing(self):
        response = self.client.get(reverse('shop:product_list'), {'q': 'teapot'})
        self.assertEqual(response.status_code, 200)
        products = list(response.context['products'])
        self.assertEqual(products, [self.teapot])
        self.assertEqual(response.context['query'], 'teapot')

    def test_query_preserved_in_context(self):
        response = self.client.get(reverse('shop:product_list'), {'q': '  tea  '})
        self.assertEqual(response.context['query'], '  tea  ')

    def test_search_form_renders_with_query_value(self):
        response = self.client.get(reverse('shop:product_list'), {'q': 'mug'})
        self.assertContains(response, 'name="q"')
        self.assertContains(response, 'value="mug"')

    def test_no_results_page_is_friendly(self):
        response = self.client.get(reverse('shop:product_list'), {'q': 'zebra'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No products match')
        self.assertNotContains(response, 'product-card')

    def test_empty_query_is_plain_catalog(self):
        response = self.client.get(reverse('shop:product_list'), {'q': ''})
        self.assertEqual(len(response.context['products']), 2)
        self.assertNotContains(response, 'catalog-search__summary')

    def test_search_combined_with_category_filter(self):
        url = reverse(
            'shop:product_list_by_category', args=[self.category.slug]
        )
        response = self.client.get(url, {'q': 'tea'})
        products = list(response.context['products'])
        self.assertIn(self.teapot, products)
        self.assertNotIn(self.hidden, products)


class PostgresSearchOnlyTests(SearchFixture):
    """Behavior that requires PostgreSQL-specific machinery.

    Skipped on the SQLite fallback (CI runs on SQLite; the compose stack
    runs Postgres), where ``icontains`` has no stemmer and no stopword
    concept.
    """

    def setUp(self):
        super().setUp()
        from django.db import connection
        if connection.vendor != 'postgresql':
            self.skipTest('PostgreSQL-only behavior')

    def test_word_form_matches_stemmed_lexeme(self):
        # The tsvector stem of "teapot" matches the query term "teapots".
        self.assertEqual(self.matching('TEAPOTS'), [self.teapot])

    def test_stopword_only_query_matches_nothing(self):
        # A query made only of stopwords has no lexemes left: it must match
        # nothing, not fall back to the whole catalog. The SQLite fallback
        # has no stopword concept, so this is asserted on Postgres only.
        self.assertEqual(self.matching('a b'), [])
        self.assertEqual(self.matching('or'), [])

    def test_punctuation_only_query_matches_nothing(self):
        # Punctuation-only tokens reduce to empty tsqueries on Postgres;
        # standalone they match nothing — assert they never match anything.
        self.assertEqual(self.matching('!@#'), [])
