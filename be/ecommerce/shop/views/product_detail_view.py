from django.contrib.auth import get_user_model
from django.shortcuts import render, get_object_or_404
from shop.forms import ProductReviewForm
from shop.models import Product
from cart.forms import CartAddProductForm
from wishlist.models import WishList, WishListItem


def product_detail(request, id, slug):
    product = get_object_or_404(Product, id=id, slug=slug, available=True)
    variants = product.variants.filter(available=True)
    cart_product_form = CartAddProductForm(
        variants=variants if variants.exists() else None
    )
    user_review = None
    if request.user.is_authenticated:
        user_review = product.reviews.filter(user=request.user).first()
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
            'wish_item': wish_item,
        }
    )
