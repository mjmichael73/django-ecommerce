from django.http import JsonResponse
from django.shortcuts import render
from cart.utils import Cart
from cart.utils.ajax import cart_summary_json
from cart.views.cart_add_view import is_ajax
from cart.forms import CartAddProductForm
from coupons.forms import CouponApplyForm


def cart_detail(request):
    cart = Cart(request)
    for item in cart:
        item['update_quantity_form'] = CartAddProductForm(
            initial={
                'quantity': item['quantity'],
                'override': True
            }
        )
    coupon_apply_form = CouponApplyForm()

    if is_ajax(request):
        return JsonResponse(cart_summary_json(cart))

    return render(request, 'cart/detail.html', {'cart': cart, 'coupon_apply_form': coupon_apply_form})
