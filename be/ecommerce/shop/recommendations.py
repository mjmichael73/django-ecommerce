"""Product recommendations (card 45ba5cfh).

Rule-based "frequently bought together" / "you might also like" engine:
same-category products whose price is within a configurable band of the
current product's price, excluding the current product and any unavailable
products.

Design (binding, from the card brief):

* Same category — a product from another category is never recommended here;
  this is the simplest, most predictable signal that matches the "bought
  together" intuition.
* Similar price range — products whose price is within
  ``RECOMMENDATION_PRICE_TOLERANCE`` (default 50 %) of the source product's
  price.  A zero-price product matches only other zero-price products.
* Deterministic ordering — by price distance (ascending), then name (asc)
  so the first N are always the same for anonymous visitors.
* Unavailable products (``available=False``) are excluded on both sides:
  the requested product must be available, and only available products are
  returned.
* Returns a fresh ``QuerySet`` (sliced to ``limit``) so callers can evaluate
  it lazily and it is re-evaluated on every request.
* No caching at this layer — recommendations are cheap (one indexed query
  over a single category) and the catalog cache invalidation signals (card
  29b8eadg) already cover product save/delete.
* Safe for anonymous visitors — no user profiling, no session, no cookies.
"""
from decimal import Decimal
from typing import Optional

from django.conf import settings
from django.db.models import DecimalField, ExpressionWrapper, F, Q
from django.db.models.functions import Abs

from shop.models import Product


def _tolerance() -> Decimal:
    """Return the price tolerance multiplier from settings (default 50 %).

    Read at call time so tests can override it via ``override_settings``.
    """
    return Decimal(str(getattr(
        settings, 'RECOMMENDATION_PRICE_TOLERANCE', '0.5',
    )))


def recommend_products(
    product: Product,
    limit: int = 4,
    *,
    category: Optional[object] = None,
):
    """Return up to ``limit`` recommended products for *product*.

    Selection rules:

    1. Same category (or an explicit ``category`` override).
    2. ``available=True`` on both the requested product and the candidates.
    3. Price within ``RECOMMENDATION_PRICE_TOLERANCE`` (±%) of *product*'s
       price: ``|price - product.price| <= product.price * tolerance``.
    4. Excludes *product* itself (even if it would otherwise match).

    Ordering is deterministic: price distance ascending, then name ascending.

    Parameters
    ----------
    product:
        The product being viewed.
    limit:
        Maximum number of products to return (default 4).
    category:
        Optional override — if given, recommend from this category instead
        of ``product.category``.  Useful for tests that want to decouple the
        recommendation category from the product's own category.

    Returns
    -------
    QuerySet[Product]
        A re-evaluable queryset of recommended products (sliced to ``limit``).
        Returns an empty queryset when the requested product is unavailable
        or has no category.  Never raises.
    """
    # An unavailable product has nothing to recommend alongside it.
    if not product.available:
        return Product.objects.none()

    target_category = category if category is not None else product.category

    if target_category is None:
        # A product without a category can't have same-category siblings.
        return Product.objects.none()

    base_price = product.price
    tolerance = _tolerance()
    threshold = base_price * tolerance

    # Build a queryset of available, same-category products excluding self.
    qs = (
        Product.objects
        .filter(
            category=target_category,
            available=True,
        )
        .exclude(pk=product.pk)
    )

    # Price band: |price - base_price| <= threshold.
    # Use a conditional to handle the zero-price edge case cleanly: when the
    # base price is 0 the band collapses to "price == 0" (threshold == 0), which
    # is correct — only free products are "similar" to a free product.
    if base_price == 0:
        qs = qs.filter(price=0)
    else:
        qs = qs.filter(
            Q(price__gte=base_price - threshold) &
            Q(price__lte=base_price + threshold)
        )

    # Annotate with the absolute price distance so we can order by it.
    # ``Abs(ExpressionWrapper(F('price') - base_price, ...))`` computes |price -
    # base_price| at the SQL level — works on PostgreSQL (the production/test
    # backend).  We keep the annotation in a private ``_price_distance`` field
    # so it is available to callers that want it but does not collide with any
    # model field.
    qs = qs.annotate(
        _price_distance=Abs(
            ExpressionWrapper(
                F('price') - base_price,
                output_field=DecimalField(max_digits=10, decimal_places=2),
            )
        ),
    ).order_by('_price_distance', 'name')

    return qs[:limit]
