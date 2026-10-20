"""Template tags for responsive product images (card 86ac598h)."""
from django import template

register = template.Library()


@register.simple_tag
def srcset(url_width_pairs):
    """Render an ``srcset`` attribute value from ``[(url, width), ...]``.

    Usage: ``srcset{% srcset product.srcset_urls %}``
    """
    if not url_width_pairs:
        return ''
    return ', '.join(f'{url} {width}w' for url, width in url_width_pairs)
