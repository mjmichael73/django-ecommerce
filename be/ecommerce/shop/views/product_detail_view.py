from django.shortcuts import render, get_object_or_404
from shop.models import Product
from cart.forms import CartAddProductForm


def product_detail(request, id, slug):
    product = get_object_or_404(Product, id=id, slug=slug, available=True)
    variants = product.variants.filter(available=True)
    cart_product_form = CartAddProductForm(
        variants=variants if variants.exists() else None
    )
    return render(
        request,
        'shop/product/detail.html',
        {
            'product': product,
            'cart_product_form': cart_product_form,
        }
    )
