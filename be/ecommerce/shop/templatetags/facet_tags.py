"""Template tags for faceted navigation (card 3g83heaa).

Provides:
* ``build_facet_url`` — generate a ``?key=value`` (or multi) query string
  that toggles a single facet value, dropping the ``page`` param so facet
  clicks always reset to page 1.
* ``dict_get`` — look up a key in a dict (used for facet counts).
* ``get_category_count`` — convenience filter for the category facet count.
"""
from django import template
from shop.facets import PRICE_BANDS, RATING_STEPS, facet_url

register = template.Library()


@register.simple_tag
def build_facet_url(get_params, facet_param, facet_value):
    """Build a query string toggling one facet value.

    ``get_params`` is the request's ``QueryDict``.
    """
    return facet_url(get_params, facet_param, facet_value)


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
