from django.shortcuts import render, get_object_or_404
from shop.models import Category, Product
from shop.search import search_products
from shop.facets import (
    parse_facets,
    apply_facets,
    apply_rating_annotation,
    facet_counts,
)


def product_list(request, category_slug=None):
    category = None
    categories = Category.objects.all()
    products = Product.objects.filter(available=True)
    # Full-text search (card bf247ef9): ?q= filters the catalog, ranked
    # best-match first on PostgreSQL; empty/whitespace/missing q shows the
    # plain listing.
    query = request.GET.get('q', '').strip()
    if query:
        products = search_products(products, query)
    if category_slug:
        category = get_object_or_404(Category, slug=category_slug)
        products = products.filter(category=category)
    # Faceted navigation (card 3g83heaa): parse facet params, annotate the
    # rating average so the rating facet can filter on it, then apply the
    # facet selection. Counts for the sidebar are computed on the base
    # queryset (before facet filtering) with each facet excluding itself,
    # so a value showing 0 is genuinely unreachable and counts never lie.
    selection = parse_facets(request.GET)
    base_products = apply_rating_annotation(products)
    if selection.is_active():
        products = apply_facets(base_products, selection)
    counts = facet_counts(base_products, selection)
    return render(
        request,
        'shop/product/list.html',
        {
            'category': category,
            'categories': categories,
            'products': products,
            'query': query,
            'facet_selection': selection,
            'facet_counts': counts,
        }
    )
