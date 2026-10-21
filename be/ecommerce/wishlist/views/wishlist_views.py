"""Wish list views (card cc26292g — Phase 2.2).

Authenticated-customer surface only, mirroring the returns/cart pattern:

* ``@login_required`` on every mutating view.
* A ``?next=``-style nudge on the product page for visitors.
* POST-only mutations (add/remove/move), each behind ``@require_POST`` and
  the ``wishlist`` rate-limit scope.
* Idempotent add (duplicate → quantity bump, no second row).
* Ownership isolation: every query is scoped to ``request.user``.

Anonymous behaviour: there is no persisted list. A logged-out visitor hitting
a wish-list URL is bounced to login with ``?next=`` pointing back at the
product detail (the nudge surface), never at the API itself.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from cart.utils import Cart
from ecommerce.rate_limit import rate_limit
from shop.models import Product, ProductVariant
from wishlist.models import WishList, WishListItem


WISHLIST_RATE_LIMIT = 'wishlist'


def _get_or_create_list(user):
    """Return the user's single wish list, creating it lazily."""
    return WishList.objects.get_or_create(user=user)[0]


def _resolve_product(product_id):
    """Fetch the product the customer is targeting.

    Only *available* products are addable — a delisted product cannot be
    wished for (same guard the catalog uses on product detail).
    """
    return get_object_or_404(Product, id=product_id, available=True)


def _resolve_variant(product, raw_variant_id):
    """Return the product's own, available variant for a submitted id.

    Anything else — blank id, non-numeric id, another product's variant, or an
    unavailable one — resolves to ``None`` so the caller falls back to a plain
    product line instead of raising (same shape as ``cart._resolve_variant``).
    """
    if not raw_variant_id:
        return None
    try:
        variant_id = int(raw_variant_id)
    except (TypeError, ValueError):
        return None
    return product.variants.filter(id=variant_id, available=True).first()


@login_required
def wishlist_detail(request):
    """Render the customer's wish list.

    The list (and its items) is fetched in a single query with
    `select_related`/`prefetch_related` so the template never triggers an N+1
    (matches the returns ``return_request_list`` select_related pattern).
    """
    try:
        wish_list = WishList.objects.select_related('user').prefetch_related(
            'items', 'items__product', 'items__variant',
        ).get(user=request.user)
    except WishList.DoesNotExist:
        wish_list = None
    return render(request, 'wishlist/detail.html', {'wish_list': wish_list})


@login_required
@rate_limit(WISHLIST_RATE_LIMIT, methods=('POST',))
@require_POST
def wishlist_add(request, product_id):
    """Add a product (optionally a variant) to the customer's wish list.

    Idempotent: re-adding the same product+variant bumps the quantity. The
    DB unique constraint is the last line of defense — ``get_or_create``
    resolves the race for the common case, but a truly concurrent twin
    (same key, same window) that slips past it is caught and retried
    transparently via a single ``IntegrityError`` → retry (mirrors the
    ``product_review`` view's one-shot replay).
    """
    product = _resolve_product(product_id)
    variant = _resolve_variant(product, request.POST.get('variant_id'))
    quantity = 1
    raw_qty = request.POST.get('quantity')
    if raw_qty:
        try:
            quantity = max(1, int(raw_qty))
        except (TypeError, ValueError):
            quantity = 1

    wish_list = _get_or_create_list(request.user)
    try:
        with transaction.atomic():
            item, created = wish_list.add(
                product=product, quantity=quantity, variant=variant,
            )
    except IntegrityError:
        # Concurrent twin that slipped past get_or_create within the same
        # atomic block: the unique constraint caught it. Retry the merge
        # once — get_or_create will now find the existing row.
        item, created = wish_list.add(
            product=product, quantity=quantity, variant=variant,
        )

    if created:
        messages.success(request, f'Added {product.name} to your wish list.')
    else:
        messages.info(
            request,
            f'{product.name} is already on your wish list (quantity now {item.quantity}).',
        )
    return redirect('wishlist:detail')


@login_required
@rate_limit(WISHLIST_RATE_LIMIT, methods=('POST',))
@require_POST
def wishlist_remove(request, product_id):
    """Remove one product line (and optional variant) from the list.

    A plain ``?variant_id=`` pins the variant line; omitting it targets the
    plain product line. Removing an absent line is a no-op (matches
    ``Cart.remove``). Unknown product/variant ids are 404 so a customer
    cannot probe a sibling's list shape.
    """
    product = _resolve_product(product_id)
    raw_variant_id = request.POST.get('variant_id')
    variant = None
    if raw_variant_id:
        try:
            variant_id = int(raw_variant_id)
        except (TypeError, ValueError):
            variant_id = None
        if variant_id is not None:
            # No availability filter: a delisted variant line must stay
            # removable (matches cart_remove_view behaviour).
            variant = product.variants.filter(id=variant_id).first()
            if variant is None:
                # A stale foreign variant id is treated as "no such line" so
                # the customer can't enumerate variants of other products.
                return HttpResponseBadRequest('Variant not found.')

    wish_list, _ = WishList.objects.get_or_create(user=request.user)
    removed = wish_list.remove(product=product, variant=variant)
    if removed:
        messages.success(request, f'Removed {product.name} from your wish list.')
    else:
        messages.info(request, f'{product.name} was not on your wish list.')
    return redirect('wishlist:detail')


@login_required
@rate_limit(WISHLIST_RATE_LIMIT, methods=('POST',))
@require_POST
def wishlist_add_to_cart(request, product_id):
    """Move *one* wish line into the cart and remove it from the list.

    The customer picks the line by product (+ optional variant). Moving a
    variant line that was deleted (variant now NULL) targets the plain
    product line, so a stale wish line still resolves predictably.
    """
    product = _resolve_product(product_id)
    raw_variant_id = request.POST.get('variant_id')
    variant = _resolve_variant(product, raw_variant_id)

    wish_list, _ = WishList.objects.get_or_create(user=request.user)
    try:
        item = wish_list.items.get(
            product=product,
            **(
                {'variant': variant} if variant is not None
                else {'variant__isnull': True}
            ),
        )
    except WishListItem.DoesNotExist:
        messages.error(request, f'{product.name} is not on your wish list.')
        return redirect('wishlist:detail')

    cart = Cart(request)
    cart.add(product=product, quantity=item.quantity, variant=variant)
    wish_list.remove(product=product, variant=variant)
    messages.success(
        request,
        f'Moved {item.quantity}× {product.name} to your cart.',
    )
    return redirect('cart:cart_detail')


@login_required
@rate_limit(WISHLIST_RATE_LIMIT, methods=('POST',))
@require_POST
def wishlist_clear(request):
    """Empty the customer's entire wish list."""
    removed, _details = WishList.objects.filter(
        user=request.user,
    ).delete()
    if removed:
        messages.success(request, 'Your wish list is now empty.')
    else:
        messages.info(request, 'Your wish list is already empty.')
    return redirect('wishlist:detail')
