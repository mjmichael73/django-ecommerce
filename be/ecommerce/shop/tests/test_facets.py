"""Faceted navigation tests (card 3g83heaa).

Tests cover:
1. parse_facets: parsing facet params, unknown-input tolerance, dedup,
   first-occurrence order, rating clamping.
2. apply_facets: OR-within / AND-across filtering, rating threshold,
   stock filter, composition with existing queryset filters.
3. apply_rating_annotation: the ``rating_avg`` annotation used by facets.
4. facet_counts: counts exclude the facet itself (others-filtered),
   selected values keep non-zero counts, unreachable values show 0.
5. facet_url / clear_all_url: query string toggling, multi-select toggle,
   single-select rating, stock binary toggle, clearing all, page param
   stripped, ``q`` preserved.
6. Template rendering: the list page renders facets, counts, disabled
   links, and compose with ``?q=``.
"""
from decimal import Decimal

from django.http import QueryDict
from django.test import TestCase, Client
from django.urls import reverse

from shop.models import Category, Product, ProductReview
from shop.facets import (
    PRICE_BANDS,
    RATING_STEPS,
    PARAM_CATEGORY,
    PARAM_PRICE,
    PARAM_RATING,
    PARAM_STOCK,
    PARAM_PAGE,
    FacetSelection,
    parse_facets,
    apply_facets,
    apply_rating_annotation,
    facet_counts,
    facet_url,
    clear_all_url,
    price_band_for,
)


class FacetFixture(TestCase):
    """Shared fixture: two categories, products spanning price bands and
    stock states, with approved/unapproved reviews for rating facets.
    """

    @classmethod
    def setUpTestData(cls):
        cls.home = Category.objects.create(name='Home', slug='home')
        cls.garden = Category.objects.create(name='Garden', slug='garden')

        # Price band under-25 (price < 25), tracked stock, good rating
        cls.mug = Product.objects.create(
            category=cls.home, name='Mug', slug='mug',
            price=Decimal('10.00'), stock=5,
        )
        # Price band 25-50 (25 <= price < 50), tracked stock
        cls.shirt = Product.objects.create(
            category=cls.home, name='Shirt', slug='shirt',
            price=Decimal('30.00'), stock=0,
        )
        # Price band 50-100 (50 <= price < 100), untracked stock
        cls.lamp = Product.objects.create(
            category=cls.garden, name='Lamp', slug='lamp',
            price=Decimal('75.00'), stock=None,
        )
        # Price band 100-plus (price >= 100), tracked stock
        cls.sofa = Product.objects.create(
            category=cls.garden, name='Sofa', slug='sofa',
            price=Decimal('150.00'), stock=2,
        )

        # Reviews for rating facets (approved by default)
        from django.contrib.auth import get_user_model
        User = get_user_model()
        cls.user1 = User.objects.create_user(
            'user1', 'u1@example.com', 'pass-phrase-99',
        )
        cls.user2 = User.objects.create_user(
            'user2', 'u2@example.com', 'pass-phrase-99',
        )
        cls.user3 = User.objects.create_user(
            'user3', 'u3@example.com', 'pass-phrase-99',
        )

        # mug: 5-star (user1) + 3-star (user2) = avg 4.0
        ProductReview.objects.create(
            product=cls.mug, user=cls.user1, rating=5, title='T', body='B',
        )
        ProductReview.objects.create(
            product=cls.mug, user=cls.user2, rating=3, title='T', body='B',
        )
        # lamp: 4-star only = avg 4.0
        ProductReview.objects.create(
            product=cls.lamp, user=cls.user3, rating=4, title='T', body='B',
        )
        # shirt: no reviews (avg None)
        # sofa: unapproved review (must not affect avg)
        ProductReview.objects.create(
            product=cls.sofa, user=cls.user3, rating=5, title='T', body='B',
            approved=False,
        )

    def _all_products(self):
        return Product.objects.filter(available=True)


class PriceBandForTests(TestCase):
    """price_band_for maps a price to exactly one band key."""

    def test_under_25(self):
        self.assertEqual(price_band_for(Decimal('0.01')), 'under-25')
        self.assertEqual(price_band_for(Decimal('24.99')), 'under-25')

    def test_25_50(self):
        self.assertEqual(price_band_for(Decimal('25.00')), '25-50')
        self.assertEqual(price_band_for(Decimal('49.99')), '25-50')

    def test_50_100(self):
        self.assertEqual(price_band_for(Decimal('50.00')), '50-100')
        self.assertEqual(price_band_for(Decimal('99.99')), '50-100')

    def test_100_plus(self):
        self.assertEqual(price_band_for(Decimal('100.00')), '100-plus')
        self.assertEqual(price_band_for(Decimal('9999.99')), '100-plus')


class ParseFacetsTests(FacetFixture):
    """parse_facets: parsing, unknown-input tolerance, dedup, ordering."""

    def test_empty_params_returns_empty_selection(self):
        selection = parse_facets(QueryDict(''))
        self.assertFalse(selection.is_active())
        self.assertEqual(selection.categories, [])
        self.assertEqual(selection.prices, [])
        self.assertIsNone(selection.rating)
        self.assertFalse(selection.stock)

    def test_none_params_returns_empty_selection(self):
        selection = parse_facets(None)
        self.assertFalse(selection.is_active())

    def test_category_param_parsed(self):
        gd = QueryDict('category=home')
        selection = parse_facets(gd)
        self.assertEqual(selection.categories, ['home'])
        self.assertTrue(selection.is_active())

    def test_category_multi_select_preserves_order(self):
        gd = QueryDict('category=garden&category=home')
        selection = parse_facets(gd)
        self.assertEqual(selection.categories, ['garden', 'home'])

    def test_category_unknown_slug_dropped(self):
        gd = QueryDict('category=nonexistent&category=home')
        selection = parse_facets(gd)
        self.assertEqual(selection.categories, ['home'])

    def test_category_dedup(self):
        gd = QueryDict('category=home&category=home')
        selection = parse_facets(gd)
        self.assertEqual(selection.categories, ['home'])

    def test_price_param_parsed(self):
        gd = QueryDict('price=25-50')
        selection = parse_facets(gd)
        self.assertEqual(selection.prices, ['25-50'])

    def test_price_multi_select(self):
        gd = QueryDict('price=25-50&price=under-25')
        selection = parse_facets(gd)
        self.assertEqual(selection.prices, ['25-50', 'under-25'])

    def test_price_unknown_band_dropped(self):
        gd = QueryDict('price=bogus&price=under-25')
        selection = parse_facets(gd)
        self.assertEqual(selection.prices, ['under-25'])

    def test_rating_parsed(self):
        gd = QueryDict('rating=4')
        selection = parse_facets(gd)
        self.assertEqual(selection.rating, 4)

    def test_rating_invalid_ignored(self):
        gd = QueryDict('rating=abc&rating=4')
        selection = parse_facets(gd)
        self.assertEqual(selection.rating, 4)

    def test_rating_out_of_range_ignored(self):
        gd = QueryDict('rating=7&rating=5')
        selection = parse_facets(gd)
        # 5 is not in RATING_STEPS (4, 3, 2), so only valid ones survive
        self.assertIsNone(selection.rating)

    def test_rating_strongest_wins(self):
        gd = QueryDict('rating=2&rating=4')
        selection = parse_facets(gd)
        self.assertEqual(selection.rating, 4)

    def test_stock_param_parsed(self):
        gd = QueryDict('stock=in')
        selection = parse_facets(gd)
        self.assertTrue(selection.stock)

    def test_stock_invalid_value_false(self):
        gd = QueryDict('stock=out')
        selection = parse_facets(gd)
        self.assertFalse(selection.stock)

    def test_combined_params(self):
        gd = QueryDict('category=home&price=under-25&rating=3&stock=in')
        selection = parse_facets(gd)
        self.assertEqual(selection.categories, ['home'])
        self.assertEqual(selection.prices, ['under-25'])
        self.assertEqual(selection.rating, 3)
        self.assertTrue(selection.stock)
        self.assertTrue(selection.is_active())


class ApplyRatingAnnotationTests(FacetFixture):
    """apply_rating_annotation adds ``rating_avg`` (None when no reviews)."""

    def test_annotates_avg_rating(self):
        qs = apply_rating_annotation(self._all_products())
        mug = {p.id: p.rating_avg for p in qs}.get(self.mug.id)
        self.assertEqual(mug, 4.0)

    def test_unrated_product_has_none(self):
        qs = apply_rating_annotation(self._all_products())
        shirt_avg = {p.id: p.rating_avg for p in qs}.get(self.shirt.id)
        self.assertIsNone(shirt_avg)

    def test_unapproved_review_excluded(self):
        qs = apply_rating_annotation(self._all_products())
        sofa_avg = {p.id: p.rating_avg for p in qs}.get(self.sofa.id)
        self.assertIsNone(sofa_avg)


class ApplyFacetsTests(FacetFixture):
    """apply_facets: OR-within / AND-across, rating, stock."""

    def _qs(self):
        return apply_rating_annotation(self._all_products())

    def test_no_selection_returns_all(self):
        sel = FacetSelection()
        result = list(apply_facets(self._qs(), sel))
        # apply_rating_annotation may change ordering; compare by name.
        self.assertEqual(
            sorted(p.name for p in result),
            sorted(p.name for p in self._all_products()),
        )

    def test_category_filter(self):
        sel = FacetSelection(categories=['home'])
        result = [p.name for p in apply_facets(self._qs(), sel)]
        self.assertEqual(sorted(result), ['Mug', 'Shirt'])

    def test_category_multi_select_or_within(self):
        sel = FacetSelection(categories=['home', 'garden'])
        result = apply_facets(self._qs(), sel)
        self.assertEqual(result.count(), 4)

    def test_price_band_filter(self):
        sel = FacetSelection(prices=['under-25'])
        result = [p.name for p in apply_facets(self._qs(), sel)]
        self.assertEqual(result, ['Mug'])

    def test_price_band_100_plus(self):
        sel = FacetSelection(prices=['100-plus'])
        result = [p.name for p in apply_facets(self._qs(), sel)]
        self.assertEqual(result, ['Sofa'])

    def test_price_multi_band_or(self):
        sel = FacetSelection(prices=['under-25', '25-50'])
        result = apply_facets(self._qs(), sel)
        self.assertEqual(sorted([p.name for p in result]), ['Mug', 'Shirt'])

    def test_rating_threshold(self):
        sel = FacetSelection(rating=4)
        result = [p.name for p in apply_facets(self._qs(), sel)]
        # mug (avg 4.0), lamp (avg 4.0) qualify; sofa unapproved -> None; shirt None
        self.assertEqual(sorted(result), ['Lamp', 'Mug'])

    def test_rating_3_includes_below(self):
        sel = FacetSelection(rating=3)
        result = [p.name for p in apply_facets(self._qs(), sel)]
        self.assertIn('Mug', result)  # avg 4.0 >= 3
        self.assertIn('Lamp', result)  # avg 4.0 >= 3

    def test_stock_in_only(self):
        sel = FacetSelection(stock=True)
        result = [p.name for p in apply_facets(self._qs(), sel)]
        # shirt has stock=0 -> out; lamp has stock=None -> in (untracked)
        self.assertIn('Mug', result)
        self.assertIn('Lamp', result)
        self.assertIn('Sofa', result)
        self.assertNotIn('Shirt', result)

    def test_and_across(self):
        sel = FacetSelection(categories=['home'], prices=['under-25'])
        result = [p.name for p in apply_facets(self._qs(), sel)]
        self.assertEqual(result, ['Mug'])

    def test_and_across_with_rating(self):
        sel = FacetSelection(categories=['home'], rating=4)
        result = [p.name for p in apply_facets(self._qs(), sel)]
        self.assertEqual(result, ['Mug'])


class FacetCountsTests(FacetFixture):
    """facet_counts: counts exclude the facet itself."""

    def _qs(self):
        return apply_rating_annotation(self._all_products())

    def test_category_counts_on_unfiltered(self):
        sel = FacetSelection()
        counts = facet_counts(self._qs(), sel)
        cat_counts = counts[PARAM_CATEGORY]
        self.assertEqual(cat_counts['home'], 2)
        self.assertEqual(cat_counts['garden'], 2)

    def test_category_counts_exclude_category_facet(self):
        # When category=home is selected, the category facet should still
        # show counts of all categories in the *other-filtered* set.
        sel = FacetSelection(categories=['home'])
        counts = facet_counts(self._qs(), sel)
        cat_counts = counts[PARAM_CATEGORY]
        # Others-filtered = no category filter -> all products
        self.assertEqual(cat_counts['home'], 2)
        self.assertEqual(cat_counts['garden'], 2)

    def test_price_counts_exclude_price_facet(self):
        sel = FacetSelection(prices=['under-25'])
        counts = facet_counts(self._qs(), sel)
        price_counts = counts[PARAM_PRICE]
        # When under-25 is selected, price facet counts the remaining
        # products (not filtered by price) across bands:
        # mug(10 -> under-25), shirt(30 -> 25-50), lamp(75 -> 50-100), sofa(150 -> 100-plus)
        self.assertEqual(price_counts['under-25'], 1)
        self.assertEqual(price_counts['25-50'], 1)
        self.assertEqual(price_counts['50-100'], 1)
        self.assertEqual(price_counts['100-plus'], 1)

    def test_rating_counts_exclude_rating_facet(self):
        sel = FacetSelection(rating=4)
        counts = facet_counts(self._qs(), sel)
        rating_counts = counts[PARAM_RATING]
        # Others-filtered = no rating filter -> counts on all products
        # Products with avg >= 4: mug (4.0), lamp (4.0) -> 2
        # Products with avg >= 3: mug (4.0), lamp (4.0) -> 2
        # Products with avg >= 2: mug (4.0), lamp (4.0) -> 2
        self.assertEqual(rating_counts[4], 2)
        self.assertEqual(rating_counts[3], 2)
        self.assertEqual(rating_counts[2], 2)

    def test_stock_count_excludes_stock_facet(self):
        sel = FacetSelection(stock=True)
        counts = facet_counts(self._qs(), sel)
        # When stock=in is selected, stock count is computed on the
        # others-filtered set (no stock filter) = all products
        # mug(5), shirt(0)->out, lamp(None)->in, sofa(2) -> 3 in stock
        self.assertEqual(counts[PARAM_STOCK], 3)

    def test_unreachable_value_count_is_zero(self):
        # Selecting garden: price facet counts are computed on the
        # garden-only queryset. Under-25 and 25-50 bands have 0 products
        # in garden (lamp is 50-100, sofa is 100-plus), so those show 0.
        sel = FacetSelection(categories=['garden'])
        counts = facet_counts(self._qs(), sel)
        price_counts = counts[PARAM_PRICE]
        self.assertEqual(price_counts['under-25'], 0)
        self.assertEqual(price_counts['25-50'], 0)
        self.assertEqual(price_counts['50-100'], 1)  # lamp at 75
        self.assertEqual(price_counts['100-plus'], 1)  # sofa at 150


class FacetUrlTests(FacetFixture):
    """facet_url: query string toggling and clearing."""

    def test_add_category_param(self):
        gd = QueryDict('')
        url = facet_url(gd, PARAM_CATEGORY, 'home')
        self.assertEqual(url, '?category=home')

    def test_toggle_category_off(self):
        gd = QueryDict('category=home')
        url = facet_url(gd, PARAM_CATEGORY, 'home')
        # Removing the only facet param -> empty query string
        self.assertEqual(url, '?')

    def test_multi_select_toggle_removes_one(self):
        gd = QueryDict('category=home&category=garden')
        url = facet_url(gd, PARAM_CATEGORY, 'home')
        # Should remove 'home', keep 'garden'
        self.assertIn('category=garden', url)
        self.assertNotIn('category=home', url)

    def test_multi_select_add_preserves_others(self):
        gd = QueryDict('category=home&price=under-25')
        url = facet_url(gd, PARAM_CATEGORY, 'garden')
        self.assertIn('category=home', url)
        self.assertIn('category=garden', url)
        self.assertIn('price=under-25', url)

    def test_rating_single_select_replaces(self):
        gd = QueryDict('rating=2')
        url = facet_url(gd, PARAM_RATING, 4)
        # Should replace: rating=2 removed, rating=4 added
        self.assertIn('rating=4', url)
        self.assertNotIn('rating=2', url)

    def test_rating_toggle_off_when_same(self):
        gd = QueryDict('rating=4')
        url = facet_url(gd, PARAM_RATING, 4)
        # Same value -> toggle off -> rating removed
        self.assertNotIn('rating=', url)

    def test_stock_toggle_on(self):
        gd = QueryDict('')
        url = facet_url(gd, PARAM_STOCK, 'in')
        self.assertIn('stock=in', url)

    def test_stock_toggle_off(self):
        gd = QueryDict('stock=in')
        url = facet_url(gd, PARAM_STOCK, 'in')
        # Same value -> toggle off
        self.assertNotIn('stock=', url)

    def test_q_preserved_on_facet_change(self):
        gd = QueryDict('q=searchterm')
        url = facet_url(gd, PARAM_PRICE, 'under-25')
        self.assertIn('q=searchterm', url)
        self.assertIn('price=under-25', url)

    def test_page_param_stripped(self):
        gd = QueryDict('page=3')
        url = facet_url(gd, PARAM_PRICE, 'under-25')
        self.assertNotIn('page=3', url)
        self.assertIn('price=under-25', url)

    def test_clear_all_url_drops_facets_keeps_q(self):
        gd = QueryDict('category=home&price=under-25&q=searchterm&page=3')
        url = clear_all_url(gd)
        self.assertIn('q=searchterm', url)
        self.assertNotIn('category=', url)
        self.assertNotIn('price=', url)
        self.assertNotIn('page=', url)
        self.assertNotIn('rating=', url)
        self.assertNotIn('stock=', url)


class FacetListViewTests(FacetFixture):
    """Template rendering: facets, counts, disabled links, q composition."""

    def test_no_filters_renders_no_refine_panel(self):
        url = reverse('shop:product_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        # Refine panel only shows when facets are active or searching
        self.assertNotIn('Refine', html)
        self.assertIn('All products', html)
        # Category counts always rendered
        self.assertIn('(2)', html)  # home and garden each have 2

    def test_category_facet_in_refine_when_active(self):
        url = reverse('shop:product_list')
        response = self.client.get(url, {'category': 'home'})
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('Refine', html)
        self.assertIn('Clear all', html)

    def test_price_facet_counts_rendered(self):
        url = reverse('shop:product_list')
        response = self.client.get(url, {'category': 'home'})
        html = response.content.decode()
        # under-25 has 1 product (mug), 25-50 has 1 (shirt)
        self.assertIn('Under $25', html)
        self.assertIn('$25 to $50', html)

    def test_disabled_facet_link_for_unreachable_value(self):
        # Select garden -> home count is 0 -> home price link should be disabled
        url = reverse('shop:product_list_by_category', args=[self.garden.slug])
        response = self.client.get(url, {'price': '100-plus'})
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        # When 100-plus is selected (sofa), under-25 has 0 products
        # The link should be aria-disabled
        self.assertIn('aria-disabled="true"', html)

    def test_facets_compose_with_search(self):
        url = reverse('shop:product_list')
        response = self.client.get(url, {'q': 'mug', 'category': 'home'})
        self.assertEqual(response.status_code, 200)
        products = list(response.context['products'])
        # Only mug matches "mug" and is in home category
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0].name, 'Mug')

    def test_rating_facet_rendered(self):
        url = reverse('shop:product_list')
        response = self.client.get(url, {'category': 'home'})
        html = response.content.decode()
        self.assertIn('Rating', html)

    def test_stock_facet_rendered(self):
        url = reverse('shop:product_list')
        response = self.client.get(url, {'category': 'home'})
        html = response.content.decode()
        self.assertIn('In stock', html)

    def test_selected_facet_shows_selected_class(self):
        url = reverse('shop:product_list')
        response = self.client.get(url, {'price': 'under-25'})
        html = response.content.decode()
        self.assertIn('selected', html)

    def test_no_500_on_malformed_facet_params(self):
        url = reverse('shop:product_list')
        response = self.client.get(url, {
            'category': 'nonexistent',
            'price': 'bogus',
            'rating': 'abc',
            'stock': 'invalid',
        })
        self.assertEqual(response.status_code, 200)
        # Should fall back to unfiltered listing
        products = list(response.context['products'])
        self.assertEqual(len(products), 4)
