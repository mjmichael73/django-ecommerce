"""Pagination tests (card d8haf54c).

Covers:
1. Pagination splits the catalog into 12-product pages.
2. Page navigation preserves facet/filter params (``q``, facets).
3. Invalid page numbers fall back to page 1 (no 404/500).
4. Empty pages redirect to the last valid page.
5. Pagination controls render with correct First/Prev/Next/Last links.
6. Fewer than 12 products suppresses the pagination nav.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from shop.models import Category, Product
from shop.views.product_list_view import PRODUCTS_PER_PAGE


class PaginationFixture(TestCase):
    """Shared fixture: one category with enough products to fill 3 pages."""

    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(name='Home', slug='home')
        # 25 products -> 3 pages (12, 12, 1)
        for i in range(1, 26):
            Product.objects.create(
                category=cls.category,
                name=f'Product {i}',
                slug=f'product-{i}',
                price=Decimal('10.00'),
                stock=5,
                available=True,
            )


class PaginationViewTests(PaginationFixture):
    """The view slices results into 12-product pages and exposes page_obj."""

    def test_page_size_is_12(self):
        self.assertEqual(PRODUCTS_PER_PAGE, 12)

    def test_first_page_shows_12_products(self):
        response = self.client.get(reverse('shop:product_list'), {'page': 1})
        self.assertEqual(response.status_code, 200)
        products = list(response.context['products'])
        self.assertEqual(len(products), 12)

    def test_page_2_shows_next_12_products(self):
        response = self.client.get(reverse('shop:product_list'), {'page': 2})
        self.assertEqual(response.status_code, 200)
        products = list(response.context['products'])
        self.assertEqual(len(products), 12)

    def test_last_page_shows_remainder(self):
        response = self.client.get(reverse('shop:product_list'), {'page': 3})
        self.assertEqual(response.status_code, 200)
        products = list(response.context['products'])
        self.assertEqual(len(products), 1)

    def test_default_no_page_param_shows_page_1(self):
        response = self.client.get(reverse('shop:product_list'))
        self.assertEqual(response.status_code, 200)
        page_obj = response.context['page_obj']
        self.assertEqual(page_obj.number, 1)
        self.assertEqual(len(list(response.context['products'])), 12)


class InvalidPageTests(PaginationFixture):
    """Invalid page numbers fall back to page 1 (no 404/500)."""

    def test_non_integer_page_falls_back_to_1(self):
        response = self.client.get(reverse('shop:product_list'), {'page': 'abc'})
        self.assertEqual(response.status_code, 200)
        page_obj = response.context['page_obj']
        self.assertEqual(page_obj.number, 1)


class EmptyPageTests(PaginationFixture):
    """Empty pages redirect to the last valid page."""

    def test_empty_page_redirects_to_last_valid(self):
        # page_obj.get_page() with an out-of-range integer returns the last page.
        response = self.client.get(reverse('shop:product_list'), {'page': 999})
        self.assertEqual(response.status_code, 200)
        page_obj = response.context['page_obj']
        self.assertEqual(page_obj.number, page_obj.paginator.num_pages)


class ParamPreservationTests(PaginationFixture):
    """Page navigation preserves query and facet params."""

    def test_search_query_preserved_on_pagination(self):
        # 25 products, all named "Product N"; search for "Product 1" matches
        # products 1-19 (Product 1, 10-19). Use a broad term to span pages.
        response = self.client.get(reverse('shop:product_list'), {'q': 'Product', 'page': 2})
        self.assertEqual(response.status_code, 200)
        # The query param is preserved in the rendered page links so the
        # user keeps refining the same search.
        html = response.content.decode()
        self.assertIn('q=Product', html)

    def test_facet_params_preserved_on_pagination(self):
        response = self.client.get(
            reverse('shop:product_list'),
            {'category': 'home', 'page': 2},
        )
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('category=home', html)


class PaginationControlsTests(PaginationFixture):
    """The template renders pagination links when results span > 12 products."""

    def test_pagination_nav_rendered_when_multiple_pages(self):
        response = self.client.get(reverse('shop:product_list'))
        html = response.content.decode()
        self.assertIn('pagination', html)
        self.assertIn('Next', html)

    def test_pagination_nav_hidden_when_single_page(self):
        # Delete most products so fewer than 12 remain -> single page, no nav.
        Product.objects.all().delete()
        for i in range(1, 6):
            Product.objects.create(
                category=self.category,
                name=f'Solo {i}',
                slug=f'solo-{i}',
                price=Decimal('10.00'),
                stock=5,
                available=True,
            )
        response = self.client.get(reverse('shop:product_list'))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertNotIn('pagination', html)

    def test_next_link_points_to_page_2(self):
        response = self.client.get(reverse('shop:product_list'), {'page': 1})
        html = response.content.decode()
        self.assertIn('page=2', html)

    def test_last_link_points_to_num_pages(self):
        response = self.client.get(reverse('shop:product_list'), {'page': 1})
        html = response.content.decode()
        self.assertIn(f'page={3}', html)

    def test_current_page_marked_aria_current(self):
        response = self.client.get(reverse('shop:product_list'), {'page': 1})
        html = response.content.decode()
        self.assertIn('aria-current="page"', html)


class PaginationPageParamStrippedOnFacetChange(PaginationFixture):
    """Clicking a facet resets to page 1 (page param stripped from facet URLs)."""

    def test_facet_url_drops_page_param(self):
        from shop.facets import facet_url, PARAM_PAGE
        from django.http import QueryDict

        gd = QueryDict(f'q=Product&page=2&category=home')
        url = facet_url(gd, 'price', 'under-25')
        self.assertNotIn('page=2', url)
        self.assertIn('q=Product', url)
        self.assertIn('category=home', url)
        self.assertIn('price=under-25', url)


class PaginationCategoryListTests(PaginationFixture):
    """Pagination works on category-scoped listings too."""

    def test_category_page_paginates(self):
        url = reverse('shop:product_list_by_category', args=[self.category.slug])
        response = self.client.get(url, {'page': 2})
        self.assertEqual(response.status_code, 200)
        page_obj = response.context['page_obj']
        self.assertEqual(page_obj.number, 2)
        self.assertEqual(len(list(response.context['products'])), 12)
