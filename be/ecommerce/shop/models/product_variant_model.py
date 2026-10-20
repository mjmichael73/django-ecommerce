from django.db import models
from shop.models import Product


class ProductVariant(models.Model):
    """One sellable variation of a product (a single free-text dimension).

    ``price`` is an override: when it is NULL the variant costs exactly what
    the product costs, so downstream code always reads ``unit_price()`` rather
    than ``price``.
    """

    product = models.ForeignKey(
        Product,
        related_name='variants',
        on_delete=models.CASCADE
    )
    name = models.CharField(max_length=100)
    sku = models.CharField(max_length=50, blank=True)
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True
    )
    available = models.BooleanField(default=True)
    stock = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text='Independent of the product; NULL = untracked (always sellable).',
    )
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['product', 'name']
        constraints = [
            models.UniqueConstraint(
                fields=['product', 'sku'],
                condition=~models.Q(sku=''),
                name='product_variant_sku_unique'
            )
        ]
        indexes = [
            models.Index(fields=['available']),
        ]

    def __str__(self):
        return f"{self.product} — {self.name}"

    def unit_price(self):
        """Effective price: the variant override, else the product price."""
        return self.price if self.price is not None else self.product.price

    def is_in_stock(self):
        """Untracked (NULL) is always sellable; tracked stock > 0 is in stock."""
        if self.stock is None:
            return True
        return self.stock > 0

    is_in_stock.boolean = True
    is_in_stock.short_description = 'In stock'
