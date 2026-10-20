from django.shortcuts import redirect, get_object_or_404
from cart.utils import Cart
from shop.models import Product
from django.views.decorators.http import require_POST


@require_POST
def cart_remove(request, product_id):
    cart = Cart(request)
    product = get_object_or_404(Product, id=product_id)
    variant = None
    raw_variant_id = request.POST.get('variant_id')
    if raw_variant_id:
        try:
            variant_id = int(raw_variant_id)
        except (TypeError, ValueError):
            variant_id = None
        if variant_id is not None:
            # No availability filter: a delisted variant line must stay removable.
            variant = product.variants.filter(id=variant_id).first()
    cart.remove(product, variant=variant)
    return redirect('cart:cart_detail')
