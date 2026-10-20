from django.http import Http404, JsonResponse
from django.shortcuts import redirect, get_object_or_404
from django.views.decorators.http import require_POST
from shop.models import Product
from cart.utils import Cart
from cart.utils.ajax import cart_error_json, cart_summary_json, format_form_errors
from cart.forms import CartAddProductForm


def is_ajax(request):
    """True when *request* was sent as an AJAX/HTMX call.

    Detects either an ``X-Requested-With: XMLHttpRequest`` header or an
    ``Accept`` header containing ``application/json``. Regular HTML form
    submissions keep the original 302-redirect behaviour unchanged.
    """
    return (
        request.headers.get('x-requested-with') == 'XMLHttpRequest'
        or 'application/json' in (request.headers.get('accept') or '')
    )


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
    try:
        product = get_object_or_404(Product, id=product_id)
    except Http404:
        if is_ajax(request):
            return cart_error_json('Product not found', status=404)
        raise

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
        if is_ajax(request):
            body = cart_summary_json(cart)
            body['quantity'] = cart.cart[
                cart._cart_key(product, variant)
            ]['quantity']
            body['product_name'] = (
                variant.name if variant else product.name
            )
            return JsonResponse(body)
    elif is_ajax(request):
        return cart_error_json(
            'Invalid quantity',
            status=400,
            errors=format_form_errors(form),
        )

    return redirect('cart:cart_detail')
