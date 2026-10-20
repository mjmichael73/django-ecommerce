from django.core.paginator import Paginator
from django.shortcuts import render, get_object_or_404
from shop.models import Category, Product
from shop.search import search_products
from shop.facets import (
    parse_facets,
    apply_facets,
    apply_rating_annotation,
    facet_counts,
)

# Number of products shown on each page of the catalog listing.
PRODUCTS_PER_PAGE = 12


def product_list(request, category_slug=None):
    category = None
    categories = Category.objects.only('id', 'name', 'slug')
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
    # Pagination (card d8haf54c): 12 products per page. An invalid page
    # number (non-integer, out of range, or empty) falls back to page 1;
    # an empty page redirects to the last valid page via the template
    # controls rather than raising. Query params (``q``, facets) ride along
    # on every page link so the filters and search stay applied as the user
    # pages through results. An explicit ordering keeps pagination
    # deterministic when search isn't ranking results (avoids
    # UnorderedObjectListWarning from the rating annotation).
    page_number = request.GET.get('page') or 1
    paginated_qs = products if query else products.order_by('name')
    paginator = Paginator(paginated_qs, PRODUCTS_PER_PAGE)
    page_obj = paginator.get_page(page_number)
    return render(
        request,
        'shop/product/list.html',
        {
            'category': category,
            'categories': categories,
            'page_obj': page_obj,
            'products': page_obj.object_list,
            'query': query,
            'facet_selection': selection,
            'facet_counts': counts,
            'products_per_page': PRODUCTS_PER_PAGE,
        }
    )
