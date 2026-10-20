from django.shortcuts import render, get_object_or_404
from shop.models import Category, Product
from shop.search import search_products


def product_list(request, category_slug=None):
    category = None
    categories = Category.objects.all()
    products = Product.objects.filter(available=True)
    # Full-text search (card bf247ef9): ?q= filters the catalog, ranked
    # best-match first on PostgreSQL; empty q shows the plain listing.
    query = request.GET.get('q', '')
    if query.strip():
        products = search_products(products, query)
    if category_slug:
        category = get_object_or_404(Category, slug=category_slug)
        products = products.filter(category=category)
    return render(
        request,
        'shop/product/list.html',
        {
            'category': category,
            'categories': categories,
            'products': products,
            'query': query,
        }
    )
