from django.contrib import admin, messages
from shop.models import Category, Product, ProductReview, ProductVariant


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug']
    prepopulated_fields = {'slug': ('name',)}


class ProductVariantAdmin(admin.TabularInline):
    model = ProductVariant
    extra = 0
    fields = ['name', 'sku', 'price', 'stock']


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = [
        'name',
        'slug',
        'price',
        'available',
        'stock',
        'is_in_stock',
        'created',
        'updated',
    ]
    list_filter = ['available', 'created', 'updated']
    list_editable = ['price', 'available']
    prepopulated_fields = {'slug': ('name',)}
    inlines = [ProductVariantAdmin]


@admin.register(ProductReview)
class ProductReviewAdmin(admin.ModelAdmin):
    """Standalone moderation surface (no inline on products) for reviews."""

    list_display = ['product', 'user', 'rating', 'approved', 'created']
    list_filter = ['rating', 'approved', 'created']
    search_fields = ['title', 'body']
    actions = ['approve_reviews', 'unapprove_reviews']

    @admin.action(description='Approve selected reviews')
    def approve_reviews(self, request, queryset):
        updated = queryset.update(approved=True)
        self.message_user(
            request,
            f'Approved {updated} review(s).',
            level=messages.SUCCESS,
        )

    @admin.action(description='Unapprove selected reviews')
    def unapprove_reviews(self, request, queryset):
        updated = queryset.update(approved=False)
        self.message_user(
            request,
            f'Unapproved {updated} review(s).',
            level=messages.SUCCESS,
        )
