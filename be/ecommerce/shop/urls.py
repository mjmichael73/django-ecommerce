from shop.views import product_list, product_detail, product_review
from django.urls import path

app_name = 'shop'

urlpatterns = [
    path('', product_list, name='product_list'),
    path('<slug:category_slug>', product_list, name='product_list_by_category'),
    path('<int:id>/<slug:slug>/', product_detail, name='product_detail'),
    path(
        'products/<int:product_id>/<slug:product_slug>/reviews/',
        product_review,
        name='product_reviews'
    ),
]
