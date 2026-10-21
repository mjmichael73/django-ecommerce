"""Performance tests for checkout and listing endpoints (card e4e4dh4g).

Phase 4.3 testing — performance baselines.

These tests guard the request cost of the two hottest code paths in the
storefront:

* ``shop:product_list`` — the catalog homepage (and per-category pages).
  A single request triggers: a Category queryset, a Product list with the
  ``rating_avg`` annotation, the facet sidebar counts (one aggregate pass per
  facet), an optional full-text search vector, and template rendering.

* ``orders:order_create`` — the checkout POST that drains the session cart
  into Order + OrderItems and queues the ``order_created`` Celery task. It
  sits behind the ``checkout`` rate limiter, so the tests override the limit
  to let every iteration through.

Design choices
--------------
* **No brittle timing assertions.** Wall-clock timings vary across CI
  workers, Docker I/O budgets, and shared Postgres. Instead each test asserts
  *structural* invariants — query counts, response status, result sizes, and
  *relative* cost (N iterations should scale sub-linearly, not regress by
  more than a documented factor).

* **``CaptureQueriesContext``** is used instead of ``assertNumQueries`` so we
  can assert ``assertLess(count, N)`` (a budget ceiling) rather than an exact
  match. Exact counts are brittle across backends and incidental middleware.

* **Rate limits are disabled** (``RATE_LIMIT_CHECKOUT=(0, 0)``) so a burst
  of checkout POSTs never hits the 429 gate. The project's rate limiter
  treats ``period <= 0`` as "disabled" (see ``rate_limit.py``), which is
  cleaner than a high but finite cap — it avoids both 429s mid-suite *and*
  leaking counter state into other test classes that share the LocMem
  cache within a single runner process.

* **Stripe is mocked** — ``order_create`` itself never talks to Stripe; that
  only happens in ``payment:process``. The Celery ``order_created.delay``
  call is patched out so no worker is needed.

Environment limitations
-----------------------
* The tests run under the standard Docker Compose stack via ``make test``
  or the equivalent ``docker compose run`` invocation, which uses the
  shared PostgreSQL instance (see AGENTS.md: "shared-services").
  PostgreSQL-only search ranking is *not* asserted here — that lives in
  ``shop/tests/test_full_text_search.py``.

* The suite always runs on the per-process LocMem cache (the test runner
  swaps it in — see ``ecommerce/test_runner.py``), so rate-limiting behaves
  deterministically and never touches the shared Redis instance.

* These are *regression* tests, not a load-generation suite. For sustained
  load testing see the roadmap note on Locust (``docs/roadmap.md`` lists
  "Performance Tests | Locust | Load baselines"). Run those against a live
  Compose stack with real Postgres.
"""
import time
from decimal import Decimal
from statistics import mean
from unittest.mock import patch

from django.core.cache import cache
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.testcases import CaptureQueriesContext
from django.urls import reverse

from orders.models import Order, OrderItem
from shop.models import Category, Product, ProductReview
from shop.views.product_list_view import PRODUCTS_PER_PAGE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_catalog(num_products=20, num_categories=4, num_reviews=4):
    """Create a realistic catalog: categories, products, and reviews.

    Returns ``(category_first, product_first, products)``.
    """
    categories = []
    for i in range(num_categories):
        categories.append(Category.objects.create(
            name=f'Category {i}',
            slug=f'cat-{i}',
        ))
    products = []
    for i in range(num_products):
        cat = categories[i % len(categories)]
        products.append(Product.objects.create(
            category=cat,
            name=f'Product {i}',
            slug=f'product-{i}',
            description=f'Description for product {i}. ' * 3,
            price=Decimal(f'{10 + i}.00'),
            stock=i % 5,  # mix of 0 (sold out) and positive (in stock)
            available=i % 6 != 0,  # ~1/6 unavailable
        ))
    # Sprinkle reviews so the rating annotation and facet counts have data
    # to aggregate. Without these the SQL budget shrinks and the tests would
    # not catch a regression that only appears when reviews are present.
    from django.contrib.auth import get_user_model
    User = get_user_model()
    reviewer = User.objects.create_user(
        'reviewer', 'r@example.com', 'pass-phrase-99',
    )
    for i in range(num_reviews):
        ProductReview.objects.create(
            product=products[i % len(products)],
            user=reviewer,
            rating=(i % 5) + 1,
            title='Great',
            body='Solid product.',
        )
    return categories[0], products[0], products


def _captured_query_count(context):
    """Return the number of SQL queries captured by ``CaptureQueriesContext``."""
    return len(context.captured_queries)


# ---------------------------------------------------------------------------
# Listing endpoint performance
# ---------------------------------------------------------------------------

@override_settings(RATE_LIMIT_CHECKOUT=(0, 0))
class ProductListPerformanceTests(TestCase):
    """Guard the SQL and time budget of the catalog listing endpoints.

    The catalog page is the storefront's single hottest path: it joins
    Category, annotates Product with an average rating, computes four facet
    count groups, and optionally runs a full-text search vector.
    """

    @classmethod
    def setUpTestData(cls):
        cls.category, cls.product, cls.products = _make_catalog()

    def setUp(self):
        self.client = Client()

    # -- Structural / query-count invariants -------------------------------

    def test_catalog_homepage_query_budget(self):
        """The plain listing must not exceed a bounded SQL budget.

        Current baseline: ~14 queries on PostgreSQL (categories, products+
        annotation, four facet count aggregates, session save, template).
        We allow generous headroom so the test catches N+1 regressions
        rather than flakily failing on incidental query changes.
        """
        with CaptureQueriesContext(connection) as context:
            response = self.client.get(reverse('shop:product_list'))
        query_count = _captured_query_count(context)
        self.assertLess(query_count, 30, f'catalog homepage issued {query_count} queries')
        self.assertEqual(response.status_code, 200)
        # The page renders up to the per-page slice, not every available
        # product — pagination caps page 1 at PRODUCTS_PER_PAGE.
        rendered_products = response.context['products']
        self.assertEqual(
            rendered_products.count(),
            min(Product.objects.filter(available=True).count(), PRODUCTS_PER_PAGE),
        )

    def test_category_page_query_budget(self):
        """Filtering by a category must not multiply query count beyond budget."""
        url = reverse('shop:product_list_by_category', args=[self.category.slug])
        with CaptureQueriesContext(connection) as context:
            response = self.client.get(url)
        query_count = _captured_query_count(context)
        self.assertLess(query_count, 30, f'category page issued {query_count} queries')
        self.assertEqual(response.status_code, 200)

    def test_product_detail_query_budget(self):
        """The detail page adds a variant subquery but stays bounded."""
        # _make_catalog marks product[0] as unavailable (i % 6 == 0), so the
        # detail view 404s it. Pick the first *available* product instead.
        available_product = Product.objects.filter(available=True).first()
        self.assertIsNotNone(available_product)
        url = reverse(
            'shop:product_detail',
            args=[available_product.id, available_product.slug],
        )
        with CaptureQueriesContext(connection) as context:
            response = self.client.get(url)
        query_count = _captured_query_count(context)
        self.assertLess(query_count, 20, f'product detail issued {query_count} queries')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['product'], available_product)

    # -- Scaling: N products should not make queries grow linearly ---------

    def test_query_count_does_not_scale_with_product_count(self):
        """A larger catalog must not multiply the query count.

        The facet counts are aggregates — they should not issue a per-product
        query. We assert that a catalog with 200 extra products still fits
        within a small, fixed budget.

        Both measured GETs run in cache-MISS mode: line-A's listing caches
        under a registered key that the ProductCounter invalidates on every
        Product.objects.create(), so creating the 200-product catalog inside
        the test would otherwise make the second GET a full re-compute while
        the baseline GET rode a warm cache — comparing miss-mode vs hit-mode.
        Clearing the cache before each measured GET puts both probes on the
        same (MISS) footing.

        Query count is capped per render by the pagination page slice
        (PRODUCTS_PER_PAGE = 12), so a per-product N+1 can inflate the larger
        catalog by at most ~12 queries while a constant render stays put —
        ``large < baseline + 8`` stops a per-product N+1 while counting the
        union render's constant badge/facet queries.
        """
        # Measure the baseline catalog (created in setUpTestData), MISS-mode.
        cache.clear()
        with CaptureQueriesContext(connection) as context:
            self.client.get(reverse('shop:product_list'))
        baseline_queries = _captured_query_count(context)

        # Build a much larger catalog.
        large_category = Category.objects.create(name='Large Cat', slug='large-cat')
        for i in range(200):
            Product.objects.create(
                category=large_category,
                name=f'Big Product {i}',
                slug=f'big-product-{i}',
                description='x' * 100,
                price=Decimal('9.99'),
                available=True,
            )

        # MISS-mode again: the creates above invalidate the listing's cache
        # keys, so this GET would be a cold re-compute anyway — clear first
        # so both probes are measured on identical (MISS) footing.
        cache.clear()
        with CaptureQueriesContext(connection) as context:
            self.client.get(reverse('shop:product_list'))
        large_queries = _captured_query_count(context)

        # Queries should grow by at most a small constant, not by O(N).
        # Both probes ran MISS-mode on the same footing (see docstring), and
        # the page slice caps any per-product N+1 at PRODUCTS_PER_PAGE:
        # +8 headroom over the baseline catches that regression while letting
        # the constant union-render queries through.
        self.assertLess(
            large_queries,
            baseline_queries + 8,
            f'catalog with 200+ products issued {large_queries} queries '
            f'vs baseline {baseline_queries}',
        )

    # -- Relative wall-clock: no gross regression -------------------------

    def test_catalog_response_is_not_slower_than_baseline(self):
        """A single request should complete well under a generous budget.

        We do not assert a hard millisecond ceiling (I/O varies), but we
        assert the request finishes in reasonable time and that 50 rapid
        sequential requests each finish under the same relative ceiling.
        """
        # Warm up (template / DB cache / connection pool).
        self.client.get(reverse('shop:product_list'))

        durations = []
        for _ in range(50):
            start = time.perf_counter()
            response = self.client.get(reverse('shop:product_list'))
            durations.append(time.perf_counter() - start)
            self.assertEqual(response.status_code, 200)

        avg = mean(durations)
        # Generous ceiling: 1 second per request for a small catalog on any
        # backend. The test exists to catch a 10x+ regression (e.g. an N+1
        # that fires per product), not to micro-benchmark.
        self.assertLess(avg, 1.0, f'avg catalog request took {avg:.3f}s')

    def test_cart_detail_is_fast(self):
        """The cart page iterates the session cart; it must stay bounded."""
        # Pre-populate a session cart with several products.
        session = self.client.session
        cart = {}
        for i, product in enumerate(self.products[:10]):
            if product.available:
                cart[str(product.id)] = {
                    'quantity': (i % 3) + 1,
                    'price': str(product.price),
                }
        session['cart'] = cart
        session.save()

        with CaptureQueriesContext(connection) as context:
            response = self.client.get(reverse('cart:cart_detail'))
        query_count = _captured_query_count(context)
        self.assertLess(query_count, 25, f'cart detail issued {query_count} queries')
        self.assertEqual(response.status_code, 200)


# ---------------------------------------------------------------------------
# Checkout endpoint performance
# ---------------------------------------------------------------------------

@override_settings(RATE_LIMIT_CHECKOUT=(0, 0))
class CheckoutPerformanceTests(TestCase):
    """Guard the request cost of the checkout POST.

    ``order_create`` reads the session cart, validates the form, creates an
    Order + one OrderItem per line, clears the cart, and fires the
    ``order_created`` Celery task. Stripe is *not* involved here — that
    happens later in ``payment:process``.
    """

    @classmethod
    def setUpTestData(cls):
        cls.category, cls.product, _products = _make_catalog()

    def setUp(self):
        self.client = Client()

    def _seed_cart(self, num_lines=5):
        """Put *num_lines* products into the session cart."""
        available = Product.objects.filter(available=True)[:num_lines]
        self.assertTrue(available.count(), 'need available products for cart')
        session = self.client.session
        session['cart'] = {}
        for product in available:
            session['cart'][str(product.id)] = {
                'quantity': 2,
                'price': str(product.price),
            }
        session.save()

    def _checkout_payload(self):
        return {
            'first_name': 'Ada',
            'last_name': 'Lovelace',
            'email': 'ada@example.com',
            'address': '1 Analytical Engine Rd',
            'postal_code': 'OX1',
            'city': 'Oxford',
        }

    @patch('orders.views.order_create_view.order_created.delay')
    def test_checkout_post_query_budget(self, _mock_delay):
        """Checkout creates an order + line items in a bounded number of queries."""
        self._seed_cart(num_lines=5)
        url = reverse('orders:order_create')

        with CaptureQueriesContext(connection) as context:
            response = self.client.post(url, self._checkout_payload())
        query_count = _captured_query_count(context)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('payment:process'))
        self.assertLess(query_count, 30, f'checkout issued {query_count} queries')
        order = Order.objects.get()
        self.assertEqual(OrderItem.objects.filter(order=order).count(), 5)

    @patch('orders.views.order_create_view.order_created.delay')
    def test_checkout_does_not_scale_queries_with_cart_size(self, _mock_delay):
        """A cart with 1 line vs. 10 lines should differ by a constant, not a factor."""
        url = reverse('orders:order_create')
        payload = self._checkout_payload()

        # Small cart.
        self._seed_cart(num_lines=1)

        with CaptureQueriesContext(connection) as context:
            self.client.post(url, payload)
        small_queries = _captured_query_count(context)

        # Large cart (fresh session).
        self.client = Client()
        self._seed_cart(num_lines=10)
        with CaptureQueriesContext(connection) as context:
            self.client.post(url, payload)
        large_queries = _captured_query_count(context)

        # The difference should be small (one extra INSERT per line, but
        # Django does batch INSERTs), not 10x the small-cart count.
        self.assertLess(
            large_queries,
            small_queries + 15,
            f'10-item cart issued {large_queries} queries vs '
            f'{small_queries} for 1-item cart',
        )

    @patch('orders.views.order_create_view.order_created.delay')
    def test_repeated_checkouts_are_stable(self, _mock_delay):
        """Each checkout is independent: no state leaks, no accumulating queries."""
        url = reverse('orders:order_create')
        payload = self._checkout_payload()

        for iteration in range(5):
            self._seed_cart(num_lines=3)
            response = self.client.post(url, payload)
            self.assertEqual(response.status_code, 302)
            # Each iteration created exactly one new order.
            self.assertEqual(Order.objects.count(), iteration + 1)

        # All orders exist and have the right number of items.
        self.assertEqual(Order.objects.count(), 5)
        for order in Order.objects.all():
            self.assertEqual(OrderItem.objects.filter(order=order).count(), 3)
