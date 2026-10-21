from django.urls import path

from wishlist.views import (
    wishlist_add,
    wishlist_add_to_cart,
    wishlist_clear,
    wishlist_detail,
    wishlist_remove,
)

app_name = 'wishlist'

urlpatterns = [
    path('', wishlist_detail, name='detail'),
    path('add/<int:product_id>', wishlist_add, name='add'),
    path('remove/<int:product_id>', wishlist_remove, name='remove'),
    path(
        'add-to-cart/<int:product_id>',
        wishlist_add_to_cart,
        name='add_to_cart',
    ),
    path('clear/', wishlist_clear, name='clear'),
]
