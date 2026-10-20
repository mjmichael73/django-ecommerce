"""Product reviews (card bd8f759h).

One review per user per product, enforced in the database via the
``productreview_unique_per_user`` unique constraint (not just at the view:
the DB is the last line of defense against races and direct writes).

``user`` uses ``on_delete=PROTECT`` on purpose: a review is part of a
product's (and a user's) history, so deleting a user who has written
reviews must fail at the admin/DB level until an operator removes the
reviews explicitly. ``CASCADE`` would silently rewrite history.
"""
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from shop.models import Product


class ProductReview(models.Model):
    """A 1-5 star rating with text, written by one user about one product.

    ``approved`` defaults to ``True`` so moderation is lenient by default:
    the flag exists so flipping a setting/default turns moderation on,
    but nothing auto-hides a review today.
    """

    product = models.ForeignKey(
        Product,
        related_name='reviews',
        on_delete=models.CASCADE
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='products_reviewed',
        on_delete=models.PROTECT,
    )
    rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    title = models.CharField(max_length=100)
    body = models.TextField(max_length=2000)
    approved = models.BooleanField(default=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created']
        constraints = [
            models.UniqueConstraint(
                fields=['product', 'user'],
                name='productreview_unique_per_user',
            )
        ]

    def __str__(self):
        return f'{self.product} — {self.rating}/5'
