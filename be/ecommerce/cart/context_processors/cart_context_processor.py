from cart.utils import Cart


def cart(request):
    return {"cart": Cart(request)}
