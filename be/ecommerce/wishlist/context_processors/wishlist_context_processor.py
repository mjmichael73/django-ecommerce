"""Template context processor for wish list (card cc26292g).

Exposes ``wishlist_item_count`` — the total *quantity* of items on the
customer's single wish list. Anonymous visitors get ``0`` (no DB query):
wish lists are authenticated-only, mirroring the cart context processor which
returns an empty cart for anonymous users rather than touching the session.

The count is the sum of line quantities (not the line count), matching
``WishList.item_count``. It is a single ``aggregate`` so the nav badge never
triggers an N+1.
"""
from django.db.models import Sum

from wishlist.models import WishList, WishListItem


def wishlist(request):
    if not request.user.is_authenticated:
        return {'wishlist_item_count': 0}
    count = WishListItem.objects.filter(list__user=request.user).aggregate(
        total=Sum('quantity'),
    )['total']
    return {'wishlist_item_count': count or 0}
