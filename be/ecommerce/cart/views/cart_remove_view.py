from django.shortcuts import redirect, get_object_or_404
from cart.utils import Cart
from shop.models import Product
from django.views.decorators.http import require_POST


@require_POST
def cart_remove(request, product_id):
    cart = Cart(request)
    product = get_object_or_404(Product, id=product_id)
    cart.remove(product)
    return redirect('cart:cart_detail')
