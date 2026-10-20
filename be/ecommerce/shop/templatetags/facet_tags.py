"""Template tags for faceted navigation (card 3g83heaa).

Provides:
* ``build_facet_url`` — generate a ``?key=value`` (or multi) query string
  that toggles a single facet value, dropping the ``page`` param so facet
  clicks always reset to page 1.
* ``build_page_query`` — generate a query string preserving all current
  filters/search (including facets) while setting a new ``page`` value;
  used by the pagination controls (card d8haf54c).
* ``dict_get`` — look up a key in a dict (used for facet counts).
* ``get_category_count`` — convenience filter for the category facet count.
"""
from django import template
from shop.facets import PRICE_BANDS, RATING_STEPS, facet_url, PARAM_PAGE

register = template.Library()


@register.simple_tag
def build_facet_url(get_params, facet_param, facet_value):
    """Build a query string toggling one facet value.

    ``get_params`` is the request's ``QueryDict``.
    """
    return facet_url(get_params, facet_param, facet_value)


@register.simple_tag
def build_page_query(get_params, page_number):
    """Build a query string preserving all filters/search for a given page.

    Keeps ``q`` and every facet param (``category``, ``price``, ``rating``,
    ``stock``) while replacing (or adding) the ``page`` param. An empty
    ``page_number`` or ``1`` yields the canonical first-page URL (no
    ``page`` param), so the first page is always clean and deduplicated.
    """
    from django.http import QueryDict

    params = QueryDict('', mutable=True)
    if get_params:
        for key in get_params:
            if key == PARAM_PAGE:
                continue
            for val in get_params.getlist(key):
                params.appendlist(key, val)

    page_str = str(page_number)
    if page_str != '1':
        params.setlist(PARAM_PAGE, [page_str])

    query_string = params.urlencode()
    return query_string


@register.simple_tag
def clear_all_facet_url(get_params):
    """Build a query string that drops all facet params (keeps ``q``)."""
    from shop.facets import clear_all_url
    return clear_all_url(get_params)


@register.filter
def dict_get(dictionary, key):
    """Look up ``key`` in ``dictionary``; return ``0`` if missing (for counts)."""
    if dictionary is None:
        return 0
    return dictionary.get(key, 0)


@register.filter
def get_category_count(category_counts, category):
    """Look up a category slug's count in the category facet dict."""
    return (category_counts or {}).get(category.slug, 0)


@register.simple_tag
def facet_prices_list():
    """Return the PRICE_BANDS list for template iteration."""
    return PRICE_BANDS


@register.simple_tag
def facet_ratings_list():
    """Return the RATING_STEPS tuple for template iteration."""
    return RATING_STEPS
