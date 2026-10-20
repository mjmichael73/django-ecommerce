"""Cache invalidation signal handlers for the shop app (card 29b8eadg).

When a Product, Category, ProductVariant, or ProductReview changes, the
cached product listings and facet counts may be stale. These handlers clear
the catalog caches so the next request re-queries the database.

Categories are a simpler case: a single global cache key is cleared on any
Category change.
"""
from __future__ import annotations

from django.db.models.signals import post_save, post_delete, pre_save
from django.dispatch import receiver

from shop.cache_keys import clear_catalog_caches
from shop.models import Category, Product, ProductVariant, ProductReview


def _invalidate_catalog_cache():
    """Invalidate all catalog caches (listings, facets, categories)."""
    clear_catalog_caches()


# Track the previous image before update so handlers can clean up stale
# thumbnails when the image changes or is removed.
@receiver(pre_save, sender=Product)
def _track_product_image(instance, **kwargs):
    try:
        instance._previous_image = Product.objects.get(pk=instance.pk).image
    except Product.DoesNotExist:
        instance._previous_image = None


def _cleanup_product_thumbnails(instance):
    """Remove generated thumbnails for the product's old image, if any."""
    previous = getattr(instance, '_previous_image', None)
    if previous:
        try:
            from shop.image_utils import cleanup_thumbnails
            cleanup_thumbnails(previous)
        except Exception:
            pass


# Product changes affect listings (availability, price, stock, category)
# and facet counts (category distribution, price bands, stock).
@receiver(post_save, sender=Product)
@receiver(post_delete, sender=Product)
def _on_product_change(instance, **_kwargs):
    _cleanup_product_thumbnails(instance)
    _invalidate_catalog_cache()


# Category changes affect the category list cache and listings that filter
# by category.
@receiver(post_save, sender=Category)
@receiver(post_delete, sender=Category)
def _on_category_change(**_kwargs):
    _invalidate_catalog_cache()


# ProductVariant changes can affect inventory and pricing signals that
# surface on the catalog listing.
@receiver(post_save, sender=ProductVariant)
@receiver(post_delete, sender=ProductVariant)
def _on_variant_change(**_kwargs):
    _invalidate_catalog_cache()


# ProductReview changes affect the rating facet (rating_avg annotation).
@receiver(post_save, sender=ProductReview)
@receiver(post_delete, sender=ProductReview)
def _on_review_change(**_kwargs):
    _invalidate_catalog_cache()

