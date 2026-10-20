from django.db import models
from shop.models import Category
from django.urls import reverse


class Product(models.Model):
    category = models.ForeignKey(
        Category,
        related_name='products',
        on_delete=models.CASCADE
    )
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200)
    image = models.ImageField(
        upload_to='products/%Y/%m/%d',
        blank=True
    )
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    available = models.BooleanField(default=True)
    stock = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text='NULL = untracked (always sellable); 0 = sold out.',
    )
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        indexes = [
            models.Index(fields=['id', 'slug']),
            models.Index(fields=['name']),
            models.Index(fields=['-created'])
        ]

    def __str__(self):
        return self.name

    def is_in_stock(self):
        """Untracked (NULL) is always sellable; tracked stock > 0 is in stock."""
        if self.stock is None:
            return True
        return self.stock > 0

    is_in_stock.boolean = True
    is_in_stock.short_description = 'In stock'

    def get_absolute_url(self):
        return reverse('shop:product_detail', args=[self.id, self.slug])
