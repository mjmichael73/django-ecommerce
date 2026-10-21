"""Admin for customer wish lists (card cc26292g).

The list itself is read-only here (it is created/managed by the customer via
the storefront); the inline ``WishListItemInline`` is the operative surface so
an operator can clean up stale lines for a deleted product. ``list`` fields
are pinned (no editable product FK) so the uniqueness constraints stay
honoured.
"""
from django.contrib import admin

from wishlist.models import WishList, WishListItem


class WishListItemInline(admin.TabularInline):
    model = WishListItem
    extra = 0
    raw_id_fields = ['product', 'variant']
    readonly_fields = ['unit_price', 'line_total', 'is_available_cell', 'created', 'updated']
    fields = ['product', 'variant', 'quantity', 'unit_price', 'line_total', 'is_available_cell']

    def is_available_cell(self, obj):
        if obj.pk is None:
            return None
        return obj.is_available()
    is_available_cell.short_description = 'Available?'
    is_available_cell.boolean = True


@admin.register(WishList)
class WishListAdmin(admin.ModelAdmin):
    list_display = ['user', 'item_count', 'created', 'updated']
    search_fields = ['user__username', 'user__email']
    readonly_fields = ['user']
    inlines = [WishListItemInline]
    list_select_related = ['user']


@admin.register(WishListItem)
class WishListItemAdmin(admin.ModelAdmin):
    """Standalone surface for searching wish lines across all lists."""

    list_display = ['id', 'list_user', 'product', 'variant', 'quantity', 'created']
    list_select_related = ['list', 'list__user', 'product', 'variant']
    search_fields = ['product__name', 'list__user__username', 'list__user__email']
    readonly_fields = ['list']
    raw_id_fields = ['product', 'variant']

    def list_user(self, obj):
        return obj.list.user
    list_user.short_description = 'Customer'
