from django.db import models
from orders.models import Order
from shop.models import Product, ProductVariant


class OrderItem(models.Model):
    order = models.ForeignKey(
        Order,
        related_name='items',
        on_delete=models.CASCADE
    )
    product = models.ForeignKey(
        Product,
        related_name='order_items',
        on_delete=models.CASCADE
    )
    variant = models.ForeignKey(
        ProductVariant,
        related_name='order_items',
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2
    )
    quantity = models.PositiveIntegerField(
        default=1
    )

    class Meta:
        # Supports looking up items for an order with product data.
        indexes = [
            models.Index(fields=['order', 'product']),
        ]

    def __str__(self):
        return str(self.id)

    def get_cost(self):
        return self.price * self.quantity
