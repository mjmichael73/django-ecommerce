from django.contrib import admin
from shop.models import Category, Product, ProductVariant


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
