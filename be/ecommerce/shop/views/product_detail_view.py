from django.db.models import Prefetch
from django.shortcuts import render, get_object_or_404
from shop.forms import ProductReviewForm
from shop.models import Product, ProductVariant
from cart.forms import CartAddProductForm


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
    return render(
        request,
        'shop/product/detail.html',
        {
            'product': product,
            'cart_product_form': cart_product_form,
            'review_form': ProductReviewForm(),
            'user_review': user_review,
        }
    )
