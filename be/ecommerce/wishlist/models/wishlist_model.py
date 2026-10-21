"""Customer wish lists (card cc26292g — Phase 2.2 search & discovery).

A wish list lives on the authenticated customer (``user`` is non-null and
owner-isolated). Guests never get a persisted wish list — they see a
"save for later" session affordance on the product page only when logged in,
and are nudged to log in to create one.

Design (one wish list per user — the canonical "Saved for later"):

* ``WishList`` — the list itself, pinned to a single user via a ``OneToOneField``.
  Only one list per customer; the admin surfaces ``items`` inline.
* ``WishListItem`` — a product (optionally a variant) plus a quantity, scoped
  to its parent list. The product FK uses ``on_delete=PROTECT``: a wish list
  line is the customer's saved intent, so deleting a product from the catalog
  must fail at the DB level until an operator clears the wish lists referencing
  it (mirroring ``ProductReview.user`` PROTECT discipline in card bd8f759h). The
  list view tolerates the protected delete by showing the dead line and letting
  the customer remove it.
* Duplicate handling: ``add`` is idempotent — re-adding the same product+variant
  bumps the quantity instead of creating a second row, and the URL/view pair is
  safe under concurrent clicks because the DB enforces uniqueness first.
"""
from django.conf import settings
from django.db import models

from shop.models import Product, ProductVariant


class WishList(models.Model):
    """A single, owner-isolated wish list per customer.

    There is exactly one list per user (created lazily on first add). The
    ``OneToOneField`` with ``related_name='wish_list'`` lets
    ``request.user.wish_list`` resolve (or raise ``DoesNotExist``) without a
    separate query, exactly like ``Order``'s ``user`` FK pattern.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        related_name='wish_list',
        on_delete=models.CASCADE,
    )
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'wish list'
        verbose_name_plural = 'wish lists'

    def __str__(self):
        return f'{self.user.username} wish list'

    def add(self, product, quantity=1, variant=None):
        """Add *product* (and optionally *variant*) to this list.

        Idempotent: if the exact product+variant line already exists, the
        quantity is bumped instead of creating a duplicate row. The
        ``get_or_create`` + separate F() update is the last-line-of-defense
        against races — the unique constraint on ``(list, product, variant)``
        makes concurrent adds merge instead of duplicating.

        ``variant`` must belong to *product* (the view asserts this; this
        method trusts its caller the way ``Cart.add`` trusts ``cart_add``).
        """
        item, created = self.items.get_or_create(
            product=product,
            variant=variant,
            defaults={'quantity': quantity},
        )
        if not created:
            # Bump the existing line's quantity with a DB-level F() so the
            # increment is atomic; F() on UPDATE is valid (only INSERT of an
            # F() value is disallowed).
            self.items.filter(pk=item.pk).update(
                quantity=models.F('quantity') + quantity,
            )
            item.refresh_from_db(fields=['quantity'])
        return item, created

    def remove(self, product, variant=None):
        """Remove the product (and variant) line from this list.

        Mirrors the cart's key shape: a plain product line is keyed by
        ``product`` with ``variant IS NULL``; a variant line is keyed by both.
        Removing an absent line is a no-op (matches ``Cart.remove``).
        """
        filters = {'product': product, 'variant': variant}
        return self.items.filter(**filters).delete()[0]

    def has_product(self, product, variant=None):
        """True when this exact product+variant line is already saved."""
        from django.db.models import Q
        return self.items.filter(
            product=product,
            **({'variant': variant} if variant is not None else {'variant__isnull': True}),
        ).exists()

    @property
    def item_count(self):
        """Total quantity of items across all lines (not the line count)."""
        return sum(item.quantity for item in self.items.all())

    def move_to_cart(self, cart, product, variant=None):
        """Append this list's line to *cart* and (by default) keep it on the list.

        Returns the cart quantity added. The caller (view) decides whether to
        also clear the wish line — ``move_to_cart`` only does the add so the
        behaviour is uniform with ``Cart.add`` and easy to test in isolation.
        """
        try:
            item = self.items.get(
                product=product,
                **({'variant': variant} if variant is not None else {'variant__isnull': True}),
            )
        except self.items.model.DoesNotExist:
            return 0
        cart.add(
            product=product,
            quantity=item.quantity,
            variant=variant,
        )
        return item.quantity


class WishListItem(models.Model):
    """One saved line in a customer's wish list: product + quantity.

    ``variant`` is optional: a ``NULL`` variant line refers to the product's
    default offering, exactly like a plain cart line. When the variant row is
    later deleted (``on_delete=SET_NULL``), the line survives (the customer's
    intent to have "something from that product" stays visible) and the list
    view can render it as "variant unavailable" — mirroring how the catalog
    treats a delisted variant.
    """

    list = models.ForeignKey(
        WishList,
        related_name='items',
        on_delete=models.CASCADE,
    )
    product = models.ForeignKey(
        Product,
        related_name='wish_items',
        on_delete=models.PROTECT,
    )
    variant = models.ForeignKey(
        ProductVariant,
        related_name='wish_items',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    quantity = models.PositiveIntegerField(default=1)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created']
        constraints = [
            models.UniqueConstraint(
                fields=['list', 'product', 'variant'],
                condition=models.Q(variant__isnull=True),
                name='wishlistitem_unique_product_per_list',
            ),
            models.UniqueConstraint(
                fields=['list', 'product', 'variant'],
                condition=~models.Q(variant__isnull=True),
                name='wishlistitem_unique_variant_per_list',
            ),
        ]
        indexes = [
            models.Index(fields=['-created']),
        ]

    def __str__(self):
        suffix = f' — {self.variant}' if self.variant_id else ''
        return f'{self.product}{suffix} ×{self.quantity}'

    @property
    def unit_price(self):
        """Read-through: the variant's unit price, or the product's price."""
        if self.variant_id is not None and self.variant is not None:
            return self.variant.unit_price()
        return self.product.price

    def line_total(self):
        return self.unit_price * self.quantity

    def is_available(self):
        """True when this line is still purchasable.

        A product marked unavailable, or an explicitly-varianted line whose
        variant was deleted or is unavailable, is flagged so the list view can
        show a "no longer available" badge instead of a stale price (mirrors
        ``Product.is_in_stock`` and the variant availability filter on the
        catalog pages).
        """
        if not self.product.available:
            return False
        if self.variant_id is not None:
            # variant_id set => the row was selected; if the FK is still live
            # and the variant is unavailable, the line is dead. A NULL variant
            # on a line that had one means the variant was deleted (SET_NULL) —
            # treat as unavailable so the customer notices.
            variant = getattr(self, 'variant', None)
            if variant is None or not variant.available:
                return False
        return True
