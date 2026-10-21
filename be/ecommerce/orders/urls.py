from django.urls import path
from orders.views import order_create, admin_order_detail, admin_order_pdf, admin_dashboard

app_name = 'orders'

urlpatterns = [
    path('create/', order_create, name='order_create'),
    path('admin/order/<int:order_id>/', admin_order_detail, name='admin_order_detail'),
    path('admin/order/<int:order_id>/pdf/', admin_order_pdf, name='admin_order_pdf'),
    path('admin/dashboard/', admin_dashboard, name='admin_dashboard'),
]
