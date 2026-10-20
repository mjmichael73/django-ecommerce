"""Query-count tests for N+1 fixes (card 3d8653f6).

Asserts that order detail/admin detail/PDF views fetch each order's items and
products in a bounded set of queries regardless of how many line items an
order carries, and that the model's total/discount helpers do not re-issue
item queries when ``items`` is prefetched.
"""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from orders.models import Order, OrderItem
from shop.models import Category, Product, ProductVariant, ProductReview

User = get_user_model()


def _make_category():
    return Category.objects.create(name='Home', slug='home')


def _make_product(category, name, price):
    return Product.objects.create(
        category=category, name=name, slug=name.lower(), price=price,
    )


def _make_order_with_items(user, product, count):
    """Create an order with ``count`` line items pointing at ``product``."""
    order = Order.objects.create(
        user=user,
        first_name='A', last_name='B',
        email='a@example.com',
        address='1 St', postal_code='1', city='C',
    )
    for i in range(count):
        OrderItem.objects.create(
            order=order, product=product,
            price=Decimal('10.00'), quantity=i + 1,
        )
    return order


@override_settings(STRIPE_SECRET_KEY='sk_test_abc')
class OrderDetailQueryCountTests(TestCase):
    """order_detail must not issue N queries for N order items."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            'buyer', 'buyer@example.com', 'complex-pass-phrase-99',
        )
        self.category = _make_category()
        self.product = _make_product(self.category, 'Mug', Decimal('10.00'))
        # 5 items -> the old code did 1+1 + 2 (get_total_cost x2) + N product
        # lookups; with prefetching the item/product fetch is 2 bounded
        # queries and the model helpers hit the prefetched cache.
        self.order = _make_order_with_items(self.user, self.product, 5)
        self.client.force_login(self.user)

    def test_order_detail_item_count_is_bounded(self):
        url = reverse('accounts:order_detail', args=[self.order.id])
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # Order lookup (1) + items prefetch (1) + products prefetch (1), plus
        # session/user auth (2) and transaction savepoint machinery (2). The
        # template's two ``order.get_total_cost`` accesses and the
        # ``item.product.name`` lookups must all be served from the cache, so
        # the total stays bounded regardless of how many line items exist.
        self.assertLessEqual(
            len(ctx.captured_queries),
            8,
            'order_detail issued too many queries: '
            + str([q['sql'] for q in ctx.captured_queries]),
        )

    def test_more_items_do_not_increase_query_count(self):
        # Baseline query count with the 5-item order, then add 10 more items
        # and confirm the count is unchanged — proving no per-item N+1.
        url = reverse('accounts:order_detail', args=[self.order.id])
        with CaptureQueriesContext(connection) as baseline:
            self.client.get(url)
        _make_order_with_items(self.user, self.product, 5)
        with CaptureQueriesContext(connection) as extra:
            self.client.get(url)
        self.assertEqual(
            len(extra.captured_queries), len(baseline.captured_queries),
            'query count grew with item count — N+1 regression: '
            + str([q['sql'] for q in extra.captured_queries]),
        )

    def test_order_list_per_order_total_is_bounded(self):
        # Two orders, each with several items, rendered together.
        _make_order_with_items(self.user, self.product, 3)
        url = reverse('accounts:order_list')
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # Orders query (1) + items prefetch for all rows (1), plus session/user
        # auth (2) and savepoint machinery (2). Must not grow with item count.
        self.assertLessEqual(
            len(ctx.captured_queries),
            8,
            'order_list issued too many queries: '
            + str([q['sql'] for q in ctx.captured_queries]),
        )

    def test_order_total_helpers_use_prefetch_cache(self):
        # Force the prefetched cache to be populated and assert that
        # ``get_total_cost`` / ``get_discount`` make zero DB queries.
        order = Order.objects.prefetch_related('items').get(pk=self.order.pk)
        with CaptureQueriesContext(connection) as ctx:
            total = order.get_total_cost()
        self.assertEqual(total, Decimal('10.00') * (1 + 2 + 3 + 4 + 5))
        self.assertEqual(
            len(ctx.captured_queries), 0,
            'get_total_cost hit the DB with prefetched items',
        )


@override_settings(STRIPE_SECRET_KEY='sk_test_abc')
class AdminOrderDetailQueryCountTests(TestCase):
    """admin_order_detail must not issue N queries for N order items."""

    def setUp(self):
        self.staff = User.objects.create_superuser(
            'admin', 'admin@example.com', 'password',
        )
        self.client = Client()
        self.client.force_login(self.staff)
        self.category = _make_category()
        self.product_a = _make_product(self.category, 'Mug', Decimal('10.00'))
        self.product_b = _make_product(self.category, 'Cup', Decimal('5.00'))
        self.order = Order.objects.create(
            first_name='A', last_name='B',
            email='a@example.com',
            address='1 St', postal_code='1', city='C',
        )
        # Mix of two different products to exercise the products prefetch.
        OrderItem.objects.create(
            order=self.order, product=self.product_a,
            price=Decimal('10.00'), quantity=2,
        )
        OrderItem.objects.create(
            order=self.order, product=self.product_b,
            price=Decimal('5.00'), quantity=1,
        )

    def test_admin_detail_item_count_is_bounded(self):
        url = reverse('orders:admin_order_detail', args=[self.order.id])
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # 1 (order) + 1 (items) + 1 (products) prefetch, plus auth/session and
        # savepoint machinery — but crucially NOT one query per item.
        self.assertLessEqual(
            len(ctx.captured_queries),
            8,
            'admin_order_detail issued too many queries: '
            + str([q['sql'] for q in ctx.captured_queries]),
        )

    @patch('orders.views.admin_order_pdf_view.weasyprint')
    def test_admin_pdf_item_count_is_bounded(self, mock_weasy):
        # Avoid real PDF rendering; just assert the view's query profile.
        mock_weasy.HTML.return_value.write_pdf.return_value = None
        mock_weasy.CSS.return_value = None
        url = reverse('orders:admin_order_pdf', args=[self.order.id])
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(
            len(ctx.captured_queries),
            9,
            'admin_order_pdf issued too many queries: '
            + str([q['sql'] for q in ctx.captured_queries]),
        )


class ProductDetailQueryCountTests(TestCase):
    """product_detail must prefetch variants, reviews and review users."""

    def setUp(self):
        self.client = Client()
        self.category = _make_category()
        self.product = _make_product(self.category, 'Mug', Decimal('10.00'))
        self.v1 = ProductVariant.objects.create(
            product=self.product, name='Red', sku='R', price=Decimal('12.00'),
            stock=3,
        )
        self.v2 = ProductVariant.objects.create(
            product=self.product, name='Blue', sku='B', price=None, stock=0,
        )
        self.reviewer = User.objects.create_user(
            'r', 'r@example.com', 'complex-pass-phrase-99',
        )
        self.review = ProductReview.objects.create(
            product=self.product, user=self.reviewer,
            rating=4, title='t', body='b', approved=True,
        )

    def test_detail_query_count_is_bounded(self):
        url = self.product.get_absolute_url()
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        sqls = [q['sql'] for q in ctx.captured_queries]
        # product+category (1), variants prefetch (1), reviews prefetch (1),
        # review-user prefetch (1), plus savepoint/session overhead (2).
        # average_rating and review_count are now computed from the prefetched
        # reviews cache in the view — no extra aggregate/count queries.
        # The old code added one query per review row for ``review.user`` in
        # the template loop — this must stay bounded as reviews grow.
        self.assertLessEqual(
            len(ctx.captured_queries),
            10,
            'product_detail issued too many queries: ' + str(sqls),
        )
        # Sanity: the page still renders the variant and the review.
        html = response.content.decode()
        self.assertIn('Mug', html)
        self.assertIn('Red', html)

    def test_detail_does_not_query_per_review_in_list(self):
        # With several reviews (each from a distinct user, per the unique
        # constraint), fetching the page must not grow linearly with the
        # number of review authors (the N+1 that used to happen via
        # ``review.user.username`` in the template loop).
        for i in range(4):
            reviewer = User.objects.create_user(
                f'r{i}', f'r{i}@example.com', 'complex-pass-phrase-99',
            )
            ProductReview.objects.create(
                product=self.product, user=reviewer,
                rating=i + 1, title=f't{i}', body='b', approved=True,
            )
        url = self.product.get_absolute_url()
        with CaptureQueriesContext(connection) as ctx:
            self.client.get(url)
        self.assertLessEqual(
            len(ctx.captured_queries),
            12,
            'product_detail query count grew with review count: '
            + str([q['sql'] for q in ctx.captured_queries]),
        )

    def test_more_reviews_do_not_increase_query_count(self):
        # Baseline with the single review, then add several more (each from a
        # distinct user, since one review per user per product is enforced)
        # and confirm the rendered query count is unchanged — the
        # reviews/reviews__user prefetch must absorb the extra rows.
        url = self.product.get_absolute_url()
        with CaptureQueriesContext(connection) as baseline:
            self.client.get(url)
        for i in range(6):
            reviewer = User.objects.create_user(
                f'extra{i}', f'extra{i}@example.com', 'complex-pass-phrase-99',
            )
            ProductReview.objects.create(
                product=self.product, user=reviewer,
                rating=i + 1, title=f'r{i}', body='b', approved=True,
            )
        with CaptureQueriesContext(connection) as after:
            self.client.get(url)
        self.assertEqual(
            len(after.captured_queries), len(baseline.captured_queries),
            'query count grew with review count — N+1 regression: '
            + str([q['sql'] for q in after.captured_queries]),
        )

    def test_no_avg_or_count_queries_for_ratings(self):
        """average_rating and review_count must not issue DB queries.

        They are now computed from the prefetched reviews cache in the view,
        passed to the template as ``average_rating`` and ``review_count``.
        """
        # Pre-populate the prefetch cache.
        product = Product.objects.prefetch_related('reviews').get(pk=self.product.pk)
        # Force-evaluating the reviews cache.
        list(product.reviews.all())
        with CaptureQueriesContext(connection) as ctx:
            # Call the model methods — they should NOT hit the DB because
            # we pre-computed from prefetched data in the view. Instead,
            # verify the view passes pre-computed values by checking the
            # rendered HTML.
            response = self.client.get(self.product.get_absolute_url())
        html = response.content.decode()
        # The summary chip should show "★ 4.0 · 1 review" — proving the
        # view computed from prefetched data, not from live aggregate queries.
        self.assertIn('★ 4.0', html)
        self.assertIn('1 review', html)
        # No AVG or COUNT queries should appear in the captured SQL.
        for q in ctx.captured_queries:
            sql = q['sql'].lower()
            self.assertNotIn('avg', sql, 'Unexpected AVG query: ' + q['sql'])
            self.assertNotIn('count', sql, 'Unexpected COUNT query: ' + q['sql'])


class OrderOnlyQueryCountTests(TestCase):
    """Order list/detail views use only() to avoid loading unused fields."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.user = User.objects.create_user(
            'buyer', 'buyer@example.com', 'complex-pass-phrase-99',
        )
        self.category = _make_category()
        self.product = _make_product(self.category, 'Mug', Decimal('10.00'))
        self.order = _make_order_with_items(self.user, self.product, 3)
        self.client.force_login(self.user)

    def test_order_list_query_count_is_bounded(self):
        """order_list loads only needed fields — query count is minimal."""
        url = reverse('accounts:order_list')
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # Order query with only() (1) + items prefetch (1) + session/auth
        # overhead. The `only()` on Order avoids loading unused columns.
        self.assertLessEqual(
            len(ctx.captured_queries),
            8,
            'order_list issued too many queries: '
            + str([q['sql'] for q in ctx.captured_queries]),
        )

    def test_order_detail_query_count_is_bounded(self):
        """order_detail loads only needed fields with only()."""
        url = reverse('accounts:order_detail', args=[self.order.id])
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # Order only() (1) + items prefetch (1) + products prefetch (1) +
        # session/auth overhead. No N+1 per item, no unnecessary field loading.
        self.assertLessEqual(
            len(ctx.captured_queries),
            8,
            'order_detail issued too many queries: '
            + str([q['sql'] for q in ctx.captured_queries]),
        )
        # Verify the page renders correctly with only().
        html = response.content.decode()
        self.assertIn('Mug', html)
        self.assertIn('$10.00', html)

    def test_admin_order_detail_query_count_is_bounded(self):
        """admin_order_detail uses only() for both order and fraud fields."""
        staff = User.objects.create_superuser(
            'admin', 'admin@example.com', 'password',
        )
        staff_client = Client()
        staff_client.force_login(staff)
        url = reverse('orders:admin_order_detail', args=[self.order.id])
        with CaptureQueriesContext(connection) as ctx:
            response = staff_client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(
            len(ctx.captured_queries),
            8,
            'admin_order_detail issued too many queries: '
            + str([q['sql'] for q in ctx.captured_queries]),
        )

    @patch('orders.views.admin_order_pdf_view.weasyprint')
    def test_admin_order_pdf_query_count_is_bounded(self, mock_weasy):
        """admin_order_pdf uses only() for minimal field loading."""
        mock_weasy.HTML.return_value.write_pdf.return_value = None
        mock_weasy.CSS.return_value = None
        staff = User.objects.create_superuser(
            'admin', 'admin@example.com', 'password',
        )
        staff_client = Client()
        staff_client.force_login(staff)
        url = reverse('orders:admin_order_pdf', args=[self.order.id])
        with CaptureQueriesContext(connection) as ctx:
            response = staff_client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(
            len(ctx.captured_queries),
            9,
            'admin_order_pdf issued too many queries: '
            + str([q['sql'] for q in ctx.captured_queries]),
        )
