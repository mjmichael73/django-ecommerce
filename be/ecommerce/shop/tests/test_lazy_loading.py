"""Tests for lazy loading and infinite scroll (card 3agb8235).

Covers:
1. The lazy-load JS file exists and contains IntersectionObserver usage.
2. The infinite-scroll JS file exists and contains fetch/IntersectionObserver usage.
3. The infinite-scroll sentinel is present in the rendered list template.
4. ``data-next-url`` is set correctly (next page URL or empty).
5. The list template source uses ``data-src`` / ``data-srcset`` on image tags.
6. The detail template source uses ``data-src`` / ``data-srcset`` on image tags.
7. The load-more button is present when there are multiple pages.
"""
import os

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from shop.views.product_list_view import PRODUCTS_PER_PAGE


class LazyLoadingAssetTests(TestCase):
    """Verify the lazy-load JS asset exists and contains required features."""

    def test_lazy_load_js_exists(self):
        js_path = os.path.join(
            settings.BASE_DIR, 'shop', 'static', 'js', 'lazy-load.js'
        )
        self.assertTrue(
            os.path.exists(js_path),
            'lazy-load.js should exist in shop/static/js/',
        )

    def test_lazy_load_js_contains_intersection_observer(self):
        js_path = os.path.join(
            settings.BASE_DIR, 'shop', 'static', 'js', 'lazy-load.js'
        )
        with open(js_path, 'r') as f:
            content = f.read()
        self.assertIn('IntersectionObserver', content)
        self.assertIn('data-src', content)
        self.assertIn('data-srcset', content)


class InfiniteScrollAssetTests(TestCase):
    """Verify the infinite-scroll JS asset exists and contains required features."""

    def test_infinite_scroll_js_exists(self):
        js_path = os.path.join(
            settings.BASE_DIR, 'shop', 'static', 'js', 'infinite-scroll.js'
        )
        self.assertTrue(
            os.path.exists(js_path),
            'infinite-scroll.js should exist in shop/static/js/',
        )

    def test_infinite_scroll_js_contains_required_features(self):
        js_path = os.path.join(
            settings.BASE_DIR, 'shop', 'static', 'js', 'infinite-scroll.js'
        )
        with open(js_path, 'r') as f:
            content = f.read()
        self.assertIn('IntersectionObserver', content)
        self.assertIn('fetch', content)
        self.assertIn('data-next-url', content)
        self.assertIn('product-card', content)
        self.assertIn('history.pushState', content)


class TemplateSourceTests(TestCase):
    """Verify the template source uses data-src / data-srcset on images."""

    def test_list_template_uses_data_src(self):
        template_path = os.path.join(
            settings.BASE_DIR, 'shop', 'templates', 'shop', 'product', 'list.html'
        )
        with open(template_path, 'r') as f:
            content = f.read()
        # The template should use data-src and data-srcset for lazy loading.
        self.assertIn('data-src', content)
        self.assertIn('data-srcset', content)
        # Should NOT use loading="lazy" on product images (replaced by JS lazy-load).
        # The no-image fallback may still use loading="lazy".
        self.assertIn('class="lazy"', content)

    def test_detail_template_uses_data_src(self):
        template_path = os.path.join(
            settings.BASE_DIR, 'shop', 'templates', 'shop', 'product', 'detail.html'
        )
        with open(template_path, 'r') as f:
            content = f.read()
        self.assertIn('data-src', content)
        self.assertIn('data-srcset', content)
        self.assertIn('class="lazy"', content)


class InfiniteScrollTemplateTests(TestCase):
    """Verify the sentinel and load-more button render correctly."""

    @classmethod
    def setUpTestData(cls):
        from decimal import Decimal
        from shop.models import Category, Product

        cls.category = Category.objects.create(name='Catalog', slug='catalog')
        # Create enough products to span 3 pages (12 per page => 25 products).
        for i in range(1, 26):
            Product.objects.create(
                category=cls.category,
                name=f'Catalog Product {i}',
                slug=f'catalog-product-{i}',
                price=Decimal('10.00'),
                stock=5,
                available=True,
            )

    def setUp(self):
        super().setUp()
        cache.clear()

    def test_sentinel_present_with_next_url(self):
        """The sentinel element exists and has a data-next-url for the next page."""
        response = self.client.get(reverse('shop:product_list'), {'page': 1})
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('infinite-scroll-sentinel', html)
        self.assertIn('data-next-url', html)
        # The next page URL should contain page=2.
        self.assertIn('page=2', html)

    def test_load_more_button_present(self):
        """The load-more button is present when there are multiple pages."""
        response = self.client.get(reverse('shop:product_list'), {'page': 1})
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('infinite-scroll-load-more', html)
        self.assertIn('Load more', html)

    def test_sentinel_empty_on_last_page(self):
        """On the last page, data-next-url is empty (no more pages)."""
        response = self.client.get(reverse('shop:product_list'), {'page': 3})
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('infinite-scroll-sentinel', html)
        # The sentinel should have an empty data-next-url.
        self.assertIn('data-next-url=""', html)

    def test_sentinel_next_url_preserves_query_params(self):
        """The next page URL preserves search params."""
        response = self.client.get(
            reverse('shop:product_list'),
            {'q': 'Catalog', 'page': 1},
        )
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('q=Catalog', html)
        self.assertIn('page=2', html)


class InfiniteScrollSinglePageTests(TestCase):
    """When there's only one page, the button is hidden and no next URL."""

    @classmethod
    def setUpTestData(cls):
        from decimal import Decimal
        from shop.models import Category, Product

        cls.category = Category.objects.create(name='Solo', slug='solo')
        for i in range(1, 6):
            Product.objects.create(
                category=cls.category,
                name=f'Solo {i}',
                slug=f'solo-{i}',
                price=Decimal('10.00'),
                stock=5,
                available=True,
            )

    def setUp(self):
        super().setUp()
        cache.clear()

    def test_no_sentinel_when_single_page(self):
        """With fewer products than PRODUCTS_PER_PAGE, no infinite scroll UI."""
        response = self.client.get(reverse('shop:product_list'), {'page': 1})
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertNotIn('infinite-scroll-sentinel', html)
        self.assertNotIn('infinite-scroll-load-more', html)


class LazyLoadScriptTagTests(TestCase):
    """Verify the script tags are rendered in the templates."""

    def test_list_template_includes_script_tags(self):
        template_path = os.path.join(
            settings.BASE_DIR, 'shop', 'templates', 'shop', 'product', 'list.html'
        )
        with open(template_path, 'r') as f:
            content = f.read()
        self.assertIn('lazy-load.js', content)
        self.assertIn('infinite-scroll.js', content)

    def test_detail_template_includes_lazy_load_script(self):
        template_path = os.path.join(
            settings.BASE_DIR, 'shop', 'templates', 'shop', 'product', 'detail.html'
        )
        with open(template_path, 'r') as f:
            content = f.read()
        self.assertIn('lazy-load.js', content)
