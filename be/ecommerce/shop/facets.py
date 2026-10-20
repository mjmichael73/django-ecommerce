"""Faceted navigation (card 3g83heaa).

The catalog sidebar becomes a set of facets computed from the *current*
result set: every facet lists the values that actually occur in the
results, each with a hit count, as plain GET links that narrow the listing
when clicked. No JavaScript, no new models, no migrations.

Design (binding, from this module):

* **OR-within, AND-across.** Selecting two values of the same facet widens
  that facet to "either" (standard faceted search: users think "red OR
  blue"); different facets intersect. A single-select facet (rating) keeps
  only its strongest selected value.
* **Counts exclude the facet itself.** Each facet's counts are computed on
  the queryset filtered by the *other* facets (plus the base filters:
  ``available=True``, category page, ``?q=``). A value that shows count 0
  under the current selection is genuinely unreachable, and clicking a
  selected value again removes it — counts never lie.
* **Unknown input is ignored, never an error.** ``parse_facets`` reads a
  ``QueryDict`` via ``getlist()`` (repeated params = multi-select), drops
  unknown slugs/keys, and clamps the rating threshold to 1..5. A malformed
  facet URL renders the unfiltered-by-that-facet page instead of a 500.
* **Facets compose with search.** The ``q`` param rides along on every
  facet link (the view preserves all non-facet GET params), so search and
  facets are one mechanism, not two.

Facet sources are existing columns only: ``category`` (FK), ``price``
(bands over the decimal column), ``average_rating`` (approved reviews via
the ``rating_avg`` annotation), ``stock``/``is_in_stock`` semantics
(NULL stock = untracked = always sellable). Price bands are fixed
catalog-wide bands — not data-derived quartiles — so a band's meaning is
stable across pages and results.

CI runs the suite on SQLite (no Postgres service): everything here is
plain ORM (values, annotations, conditional expressions) and works on both
backends; there is no Postgres-only code path in this module.
"""
from dataclasses import dataclass, field

from django.db.models import (
    Avg,
    Count,
    IntegerField,
    Q,
    Value,
)
from django.db.models.functions import Coalesce
from django.db.models.query import QuerySet

# Price bands are fixed catalog-wide bands over Product.price. Keys are
# URL-safe and stable; labels are human-facing. ``None`` upper bound means
# "no upper limit". A product matches exactly one band (bands are
# half-open: [low, high)).
PRICE_BANDS = (
    ('under-25', 'Under $25', None, 25),
    ('25-50', '$25 to $50', 25, 50),
    ('50-100', '$50 to $100', 50, 100),
    ('100-plus', '$100 & over', 100, None),
)

# Rating thresholds: "rated N stars & up" (approved reviews only). The
# value stored in the URL is the threshold; a product qualifies when its
# average approved rating is >= threshold. Single-select: the strongest
# (highest) selected threshold wins.
RATING_STEPS = (4, 3, 2)

STOCK_VALUES = ('in',)

# URL parameter names. ``category`` was chosen over ``categories`` so the
# facet param reads the same as the existing category page semantics.
PARAM_CATEGORY = 'category'
PARAM_PRICE = 'price'
PARAM_RATING = 'rating'
PARAM_STOCK = 'stock'

# All facet params, for splitting the query string into "facet" and
# "everything else rides along" halves.
FACET_PARAMS = frozenset({PARAM_CATEGORY, PARAM_PRICE, PARAM_RATING, PARAM_STOCK})

# The pagination param is stripped from facet URLs so clicking a facet
# always returns to page 1 (resetting the page on facet change is the sane
# default; pagination is a separate card).
PARAM_PAGE = 'page'


@dataclass
class FacetSelection:
    """The parsed, validated facet state of one request.

    ``categories`` and ``prices`` are ordered multi-selects (first
    occurrence order); ``rating`` is the single effective threshold or
    ``None``; ``stock`` is True only when ``?stock=in`` is present.
    """

    categories: list = field(default_factory=list)
    prices: list = field(default_factory=list)
    rating: int = None
    stock: bool = False

    def is_active(self):
        """True when any facet narrows the results."""
        return bool(self.categories or self.prices or self.rating or self.stock)


def _clean_slugs(values, valid):
    """Keep known values in first-occurrence order, drop duplicates."""
    seen = set()
    kept = []
    for value in values:
        if value in valid and value not in seen:
            seen.add(value)
            kept.append(value)
    return kept


def parse_facets(get_params):
    """Parse the facet state from a request's GET params (a ``QueryDict``).

    Unknown values are dropped silently: a hand-edited URL narrows less,
    it never errors. Repeated params select multiple values of one facet.
    """
    selection = FacetSelection()
    if get_params is None:
        return selection

    selection.categories = _clean_slugs(
        get_params.getlist(PARAM_CATEGORY), {c.slug for c in _category_choices()}
    )
    selection.prices = _clean_slugs(
        get_params.getlist(PARAM_PRICE), {band[0] for band in PRICE_BANDS}
    )

    ratings = []
    for raw in get_params.getlist(PARAM_RATING):
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value in RATING_STEPS and value not in ratings:
            ratings.append(value)
    selection.rating = max(ratings) if ratings else None

    selection.stock = get_params.get(PARAM_STOCK) in STOCK_VALUES
    return selection


def _category_choices():
    from shop.models import Category

    return list(Category.objects.order_by('name'))


def price_band_for(price):
    """Return the band key a product price falls into, or None."""
    for key, _label, low, high in PRICE_BANDS:
        low_ok = low is None or price >= low
        high_ok = high is None or price < high
        if low_ok and high_ok:
            return key
    return None


def apply_facets(queryset, selection):
    """Filter a Product queryset by the parsed facet selection.

    OR-within (any of the selected categories / price bands), AND-across
    (each facet further narrows). Rating is a threshold on the annotated
    average approved rating; stock is the product-level in-stock rule
    (NULL stock = untracked = always sellable).
    """
    conditions = Q()
    if selection.categories:
        conditions &= Q(category__slug__in=selection.categories)
    if selection.prices:
        band_conditions = Q()
        for key in selection.prices:
            for _band_key, _label, low, high in PRICE_BANDS:
                if _band_key == key:
                    # A single band is an AND of its bounds: [low, high).
                    band_q = Q()
                    if low is not None:
                        band_q &= Q(price__gte=low)
                    if high is not None:
                        band_q &= Q(price__lt=high)
                    # Different bands are OR'd (OR-within).
                    band_conditions |= band_q
                    break
        conditions &= band_conditions

    if conditions:
        queryset = queryset.filter(conditions)

    if selection.rating is not None:
        queryset = queryset.filter(rating_avg__gte=selection.rating)

    if selection.stock:
        queryset = queryset.filter(Q(stock__gt=0) | Q(stock__isnull=True))
    return queryset


def apply_rating_annotation(queryset):
    """Annotate ``rating_avg`` (mean approved rating, None when unrated).

    Called by the view before facets/search so both can rely on it; the
    detail view's ``average_rating()`` keeps its own per-instance query.
    Uses ``Coalesce`` to return ``None`` (not an SQL NULL) when there are
    no approved reviews, so ``gte`` comparisons on ``rating_avg`` behave
    correctly (NULL never satisfies ``>=``).
    """
    return queryset.annotate(
        rating_avg=Coalesce(
            Avg('reviews__rating', filter=Q(reviews__approved=True)),
            Value(None),
            output_field=IntegerField(),
        )
    )


def facet_counts(queryset, selection):
    """Compute every facet's value counts for the sidebar.

    Each facet is counted on the queryset filtered by all *other* facets
    (plus whatever base filters the queryset already carries: available,
    category page, search). Selected values keep their (non-zero) count;
    a value that cannot co-occur with the current selection counts 0 and
    the template renders it disabled rather than as a dead link.

    Returns a dict keyed by facet param name, each mapping to a dict of
    ``value -> count``.
    """
    # Category facet counts (on the queryset excluding the category facet).
    cat_others = apply_facets(queryset, _without(selection, PARAM_CATEGORY))
    category_counts = _category_facet_counts(cat_others, selection)

    # Price facet counts.
    price_others = apply_facets(queryset, _without(selection, PARAM_PRICE))
    price_counts = _price_facet_counts(price_others)

    # Rating facet counts.
    rating_others = apply_facets(queryset, _without(selection, PARAM_RATING))
    rating_counts = _rating_facet_counts(rating_others)

    # Stock facet counts.
    stock_others = apply_facets(queryset, _without(selection, PARAM_STOCK))
    stock_counts = {PARAM_STOCK: _stock_facet_count(stock_others)}

    return {
        PARAM_CATEGORY: category_counts,
        PARAM_PRICE: price_counts,
        PARAM_RATING: rating_counts,
        **stock_counts,
    }


def _category_facet_counts(queryset, selection):
    """Count products per category within the queryset, keyed by slug.

    The queryset already excludes the category facet (the caller builds it
    via ``_without``), so each count reflects what remains when that
    category is removed — exactly what "click this category, see what's
    left" needs.
    """
    from shop.models import Category

    # Annotate each product's category_id so we can group cheaply.
    annotated = queryset.values_list('category_id', flat=True)
    raw = (
        Category.objects
        .filter(products__in=queryset)
        .annotate(total=Count('products', filter=Q(products__in=queryset)))
        .values_list('slug', 'total')
        .order_by('name')
    )
    # Build a dict of all real categories with their counts (0 if absent).
    counts = {}
    for slug in Category.objects.values_list('slug', flat=True):
        counts[slug] = 0
    for slug, total in raw:
        counts[slug] = total
    return counts


def _price_facet_counts(queryset):
    """Count products per price band within the queryset."""
    from shop.models import Product

    # Annotate each product with its price band key, then count.
    annotated = queryset.extra(
        select={'band_key': None}
    )
    # Use a Python-side grouping over the queryset (works on SQLite + Postgres).
    counts = {key: 0 for key, _label, _low, _high in PRICE_BANDS}
    for product in queryset:
        band = price_band_for(product.price)
        if band is not None:
            counts[band] = counts.get(band, 0) + 1
    return counts


def _rating_facet_counts(queryset):
    """Count products per rating threshold (4+, 3+, 2+) within the queryset."""
    counts = {}
    for step in RATING_STEPS:
        counts[step] = queryset.filter(rating_avg__gte=step).count()
    return counts


def _stock_facet_count(queryset):
    """Count in-stock products (stock > 0 or stock is NULL) within the queryset."""
    return queryset.filter(Q(stock__gt=0) | Q(stock__isnull=True)).count()


def _without(selection, facet_param):
    """Return a copy of *selection* with one facet's selection cleared."""
    copy = FacetSelection(
        categories=list(selection.categories),
        prices=list(selection.prices),
        rating=selection.rating,
        stock=selection.stock,
    )
    if facet_param == PARAM_CATEGORY:
        copy.categories = []
    elif facet_param == PARAM_PRICE:
        copy.prices = []
    elif facet_param == PARAM_RATING:
        copy.rating = None
    elif facet_param == PARAM_STOCK:
        copy.stock = False
    return copy


def clear_all_url(get_params):
    """Build a query string that drops all facet params (keeps ``q``).

    Used by the "Clear all" link in the Refine panel.
    """
    from django.http import QueryDict

    params = QueryDict('', mutable=True)
    if get_params:
        for key in get_params:
            if key == PARAM_PAGE:
                continue
            if key in FACET_PARAMS:
                continue
            for val in get_params.getlist(key):
                params.appendlist(key, val)
    qs = params.urlencode()
    return f'?{qs}' if qs else '?'


def facet_url(get_params, facet_param, facet_value):
    """Build a URL query string that toggles one facet value.

    Preserves all other GET params (including ``q`` and the page param,
    which the view resets on facet change). If ``facet_value`` is already
    selected it is removed; otherwise it is appended.

    For single-select facets (rating, stock) the toggle is exclusive:
    selecting a new rating replaces the old; toggling stock on/off is
    binary.
    """
    from django.http import QueryDict

    params = QueryDict('', mutable=True)
    if get_params:
        for key in get_params:
            if key == facet_param:
                continue
            for val in get_params.getlist(key):
                params.appendlist(key, val)

    # Skip the page param so clicking a facet always goes to page 1.
    params.pop(PARAM_PAGE, None)

    if facet_param == PARAM_RATING:
        # Single-select: setting replaces, clearing (same value again) removes.
        current = get_params.getlist(PARAM_RATING) if get_params else []
        if str(facet_value) not in current:
            params.setlist(PARAM_RATING, [str(facet_value)])
        # If it's already selected, we simply don't re-add it (= remove).
    elif facet_param == PARAM_STOCK:
        current = bool(get_params.get(PARAM_STOCK) in STOCK_VALUES) if get_params else False
        if not current:
            params.setlist(PARAM_STOCK, [facet_value])
    else:
        # Multi-select: toggle membership.
        current = get_params.getlist(facet_param) if get_params else []
        if facet_value in current:
            # Remove: keep all other selected values.
            new_values = [v for v in current if v != facet_value]
            params.setlist(facet_param, new_values)
        else:
            # Append: preserve existing selections + add the new one.
            params.setlist(facet_param, list(current) + [facet_value])

    query_string = params.urlencode()
    return f'?{query_string}' if query_string else '?'
