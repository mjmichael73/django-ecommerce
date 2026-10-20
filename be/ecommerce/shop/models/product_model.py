from django.db import models
from django.contrib.postgres.search import SearchVectorField
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
    # PostgreSQL tsvector maintained by the trigger in migration 0005 —
    # never written by application code (card bf247ef9). On the SQLite
    # fallback backend the column is a plain text field and unused.
    search_vector = SearchVectorField(null=True, editable=False)

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

    def average_rating(self):
        """Mean rating of approved reviews, or None when there are none yet.

        Unapproved reviews (``approved=False``) are excluded so moderation
        can hide a review from the aggregate without touching the row.
        """
        return self.reviews.exclude(approved=False).aggregate(
            rating=models.Avg('rating')
        )['rating']

    def review_count(self):
        """Number of approved reviews (an int, 0 when there are none)."""
        return self.reviews.exclude(approved=False).count()
