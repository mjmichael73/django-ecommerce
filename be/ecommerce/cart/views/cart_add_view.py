from django.shortcuts import redirect, get_object_or_404
from django.views.decorators.http import require_POST
from shop.models import Product
from cart.utils import Cart
from cart.forms import CartAddProductForm


def _resolve_variant(product, raw_variant_id):
    """Return the product's own, available variant for a submitted id.

    Anything else — a blank id, a non-numeric id, another product's variant or
    an unavailable one — resolves to ``None`` so the caller falls back to the
    plain product line instead of raising.
    """
    if not raw_variant_id:
        return None
    try:
        variant_id = int(raw_variant_id)
    except (TypeError, ValueError):
        return None
    return product.variants.filter(id=variant_id, available=True).first()


@require_POST
def cart_add(request, product_id):
    cart = Cart(request)
    product = get_object_or_404(Product, id=product_id)
    variants = product.variants.filter(available=True)
    variant = _resolve_variant(
        product,
        request.POST.get('variant') or request.POST.get('variant_id'),
    )
    data = request.POST.copy()
    if variant is None:
        # A stray/foreign/unavailable variant id must not invalidate the whole
        # form: drop it and add the default (plain product) line.
        for key in ('variant', 'variant_id'):
            if key in data:
                del data[key]
    form = CartAddProductForm(data, variants=variants)
    if form.is_valid():
        cd = form.cleaned_data
        cart.add(
            product=product,
            quantity=cd['quantity'],
            override_quantity=cd['override'],
            variant=variant,
        )
    return redirect('cart:cart_detail')
