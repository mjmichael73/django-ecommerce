"""Full-text search tests (card bf247ef9).

``shop.search.search_products`` behind the catalog ``?q=`` parameter:
PostgreSQL-native on-the-fly SearchVector (name weight A, description
weight B, config 'english'), plain SearchQuery, ranked ordering
(``-rank, name``). CI runs on SQLite, where the same call degrades to an
``icontains`` OR — the contract tests below pass on both backends; the
stemming/ranking tests assert Postgres-only behavior and skip elsewhere.
"""
from decimal import Decimal

from django.db import connection
from django.test import TestCase
from django.urls import reverse

from shop.models import Category, Product
from shop.search import search_products


class SearchFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(name='Home', slug='home')
        cls.other = Category.objects.create(name='Garden', slug='garden')
        cls.runner = Product.objects.create(
            category=cls.category, name='Running Shoes', slug='running-shoes',
            description='Lightweight shoes for running on trails.',
            price=Decimal('80.00'),
        )
        cls.shirt = Product.objects.create(
            category=cls.category, name='Cotton Shirt', slug='cotton-shirt',
            description='A soft shirt; running gear for warm days.',
            price=Decimal('25.00'),
        )
        cls.mug = Product.objects.create(
            category=cls.other, name='Ceramic Mug', slug='ceramic-mug',
            description='A sturdy mug for hot tea.',
            price=Decimal('10.00'),
        )
        cls.hidden = Product.objects.create(
            category=cls.category, name='Hidden Running Vest', slug='hidden',
            description='Running gear not for sale.',
            price=Decimal('20.00'), available=False,
        )

    def matching(self, query):
        base = Product.objects.filter(available=True)
        return list(search_products(base, query))

    def is_postgres(self):
        return connection.vendor == 'postgresql'


class SearchProductsTests(SearchFixture):
    def test_empty_query_returns_queryset_unchanged(self):
        base = Product.objects.filter(available=True)
        self.assertEqual(list(search_products(base, '')), list(base))
        self.assertEqual(list(search_products(base, '   ')), list(base))

    def test_name_match_found(self):
        self.assertEqual(self.matching('mug'), [self.mug])

    def test_description_only_match_found(self):
        # 'sturdy' appears only in the mug's description.
        self.assertEqual(self.matching('sturdy'), [self.mug])

    def test_stemming_matches_word_forms(self):
        # 'run' stems to the same lexeme as 'running' (Postgres); on the
        # SQLite fallback 'run' is a substring of 'running', so both
        # backends find the runner.
        results = self.matching('run')
        self.assertIn(self.runner, results)
        self.assertNotIn(self.hidden, results)

    def test_name_and_description_matches_found(self):
        # 'running' matches the runner's name and the shirt's description.
        results = self.matching('running')
        self.assertIn(self.runner, results)
        self.assertIn(self.shirt, results)

    def test_ordering_is_deterministic(self):
        # Same query twice must return the same order.
        self.assertEqual(self.matching('running'), self.matching('running'))

    def test_unavailable_products_never_match(self):
        self.assertNotIn(self.hidden, self.matching('running'))
        self.assertNotIn(self.hidden, self.matching('vest'))

    def test_no_match_is_empty(self):
        self.assertEqual(self.matching('zebra'), [])


class SearchViewTests(SearchFixture):
    def test_query_parameter_filters_listing(self):
        response = self.client.get(reverse('shop:product_list'), {'q': 'running'})
        self.assertEqual(response.status_code, 200)
        products = list(response.context['products'])
        self.assertIn(self.runner, products)
        self.assertIn(self.shirt, products)
        self.assertNotIn(self.hidden, products)
        self.assertNotIn(self.mug, products)
        self.assertEqual(response.context['query'], 'running')

    def test_missing_q_renders_plain_listing(self):
        response = self.client.get(reverse('shop:product_list'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['products']), 3)

    def test_empty_and_whitespace_q_render_plain_listing(self):
        for q in ('', '   '):
            response = self.client.get(reverse('shop:product_list'), {'q': q})
            self.assertEqual(len(response.context['products']), 3, q)

    def test_query_refills_input(self):
        response = self.client.get(reverse('shop:product_list'), {'q': 'mug'})
        self.assertContains(response, 'name="q"')
        self.assertContains(response, 'value="mug"')

    def test_no_match_renders_search_empty_message(self):
        response = self.client.get(reverse('shop:product_list'), {'q': 'zebra'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No products match your search.')
        self.assertNotContains(response, 'product-card')

    def test_category_empty_message_distinct_from_search(self):
        # Without q, the category empty text stays the original one.
        url = reverse('shop:product_list_by_category', args=[self.other.slug])
        response = self.client.get(url)
        self.assertNotContains(response, 'No products match your search.')

    def test_search_composes_with_category_filter(self):
        url = reverse('shop:product_list_by_category', args=[self.category.slug])
        response = self.client.get(url, {'q': 'mug'})
        # The mug is in another category: filtered out even though it matches.
        self.assertNotContains(response, 'product-card')
        self.assertContains(response, 'No products match your search.')

    def test_query_echo_is_autoescaped(self):
        response = self.client.get(
            reverse('shop:product_list'), {'q': '<script>alert(1)</script>'}
        )
        self.assertEqual(response.status_code, 200)
        # Raw markup never reaches the client; the escaped entities do.
        self.assertNotContains(response, '<script>alert(1)</script>')
        self.assertContains(response, '&lt;script&gt;')

    def test_product_name_rendering_is_autoescaped(self):
        Product.objects.create(
            category=self.category, name='<script>name</script>',
            slug='script-name', description='x', price=Decimal('1.00'),
        )
        # The plain catalog lists the product: the raw markup must not
        # reach the client, the escaped entities must.
        response = self.client.get(reverse('shop:product_list'))
        self.assertNotContains(response, '<script>name</script>')
        self.assertContains(response, '&lt;script&gt;name&lt;/script&gt;')


class PostgresSearchOnlyTests(SearchFixture):
    """Behavior that requires PostgreSQL (stemming, ranking, plain parser).

    Skipped on the SQLite fallback (CI has no Postgres service).
    """

    def setUp(self):
        super().setUp()
        if not self.is_postgres():
            self.skipTest('PostgreSQL-only behavior')

    def test_stemming_matches_word_forms(self):
        # The stem of 'run' matches 'running' in the runner's name AND in
        # the shirt's description ('running gear for warm days').
        self.assertEqual(self.matching('run'), [self.runner, self.shirt])

    def test_name_match_ranks_above_description_match(self):
        self.assertEqual(self.matching('running'), [self.runner, self.shirt])

    def test_stopword_only_query_matches_nothing(self):
        # A query made only of stopwords has no lexemes left under the
        # plain parser: it matches nothing, not the whole catalog.
        self.assertEqual(self.matching('a b'), [])
        self.assertEqual(self.matching('or'), [])
