from django.db.models import Prefetch
from django.shortcuts import render, get_object_or_404
from shop.forms import ProductReviewForm
from shop.models import Product, ProductVariant
from shop.recommendations import recommend_products
from cart.forms import CartAddProductForm
from wishlist.models import WishList, WishListItem


def _approved_review_stats(prefetched_reviews):
    """Compute (average_rating, review_count) from prefetched review instances.

    Walks the prefetched ``reviews`` cache instead of issuing extra
    aggregate/count queries like ``product.average_rating()`` and
    ``product.review_count()``.
    """
    approved_ratings = [
        r.rating for r in prefetched_reviews if r.approved
    ]
    count = len(approved_ratings)
    if count == 0:
        return None, 0
    return sum(approved_ratings) / count, count


def product_detail(request, id, slug):
    product = get_object_or_404(
        Product.objects.select_related('category').prefetch_related(
            Prefetch(
                'variants',
                queryset=ProductVariant.objects.filter(available=True),
            ),
            'reviews',
            'reviews__user',
        ),
        id=id,
        slug=slug,
        available=True,
    )
    # ``variants`` is prefetched (filtered to available above), so
    # ``variants.exists()`` and the form's ModelChoiceField iteration both
    # read the cache instead of issuing per-access queries.
    variants = product.variants.all()
    cart_product_form = CartAddProductForm(
        variants=variants if variants else None,
    )
    user_review = None
    if request.user.is_authenticated:
        # ``reviews`` is prefetched; walk the cache instead of a per-user
        # ``reviews.filter(user=...)`` query.
        for review in product.reviews.all():
            if review.user_id == request.user.id:
                user_review = review
                break
    # Compute average_rating and review_count from the prefetched reviews
    # cache, eliminating the 2 extra queries that the template's
    # ``product.average_rating()`` / ``product.review_count()`` would issue.
    average_rating, review_count = _approved_review_stats(
        product.reviews.all()
    )
    # Product recommendations (card 45ba5cfh): same-category products within a
    # configurable price band of the current product.  One indexed query.
    recommendations = recommend_products(product)
    # Wish list state (card cc26292g): surface the saved line so the
    # detail page can render a "remove / already saved" affordance instead of
    # a second "add to wish list" button. Anonymous users get a login nudge.
    wish_item = None
    if request.user.is_authenticated:
        try:
            wish_item = request.user.wish_list.items.get(
                product=product, variant__isnull=True,
            )
        except (WishListItem.DoesNotExist, WishList.DoesNotExist):
            wish_item = None
    return render(
        request,
        'shop/product/detail.html',
        {
            'product': product,
            'cart_product_form': cart_product_form,
            'review_form': ProductReviewForm(),
            'user_review': user_review,
            'average_rating': average_rating,
            'review_count': review_count,
            'recommendations': recommendations,
            'wish_item': wish_item,
        }
    )
