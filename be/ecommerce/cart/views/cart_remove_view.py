from django.http import Http404, JsonResponse
from django.shortcuts import redirect, get_object_or_404
from cart.utils import Cart
from cart.utils.ajax import cart_error_json, cart_summary_json
from cart.views.cart_add_view import is_ajax
from shop.models import Product
from django.views.decorators.http import require_POST


@require_POST
def cart_remove(request, product_id):
    cart = Cart(request)
    try:
        product = get_object_or_404(Product, id=product_id)
    except Http404:
        if is_ajax(request):
            return cart_error_json('Product not found', status=404)
        raise

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

    if is_ajax(request):
        return JsonResponse(cart_summary_json(cart))

    return redirect('cart:cart_detail')
