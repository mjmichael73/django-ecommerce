from django.core.cache import cache
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
from shop.cache_keys import (
    cache_key_products,
    cache_key_facets,
    cache_key_categories,
    register_cache_key,
    PRODUCTS_TTL,
    CATEGORIES_TTL,
)

# Number of products shown on each page of the catalog listing.
PRODUCTS_PER_PAGE = 12

# Sentinel for cache misses.
_CACHE_MISS = object()


class _CachedPage:
    """Lightweight stand-in for a ``django.core.paginator.Page``.

    When serving from cache we can't easily reconstruct a full ``Page`` (the
    ``Paginator`` would re-slice a short list). This object exposes the same
    attributes the template reads: ``object_list``, ``number``,
    ``paginator`` (with ``count``, ``num_pages``, ``page_range``,
    ``per_page``), and the ``has_previous``/``has_next``/``previous_page_number``
    methods.

    Unlike a real ``Page``, ``object_list`` is a list of plain dicts (cached
    product data) rather than model instances. The template only reads
    ``name``, ``price``, ``get_absolute_url`` (via slug), ``image.url``, and
    ``slug`` — all of which are stored in the dict.
    """

    def __init__(self, object_list, number, count, per_page):
        self.object_list = object_list
        self.number = number
        self.paginator = _CachedPaginator(count, per_page)

    def has_previous(self):
        return self.number > 1

    def has_next(self):
        return self.number < self.paginator.num_pages

    def has_other_pages(self):
        return self.has_previous() or self.has_next()

    def previous_page_number(self):
        if not self.has_previous():
            from django.core.paginator import EmptyPage
            raise EmptyPage("Page previous does not exist.")
        return self.number - 1

    def next_page_number(self):
        if not self.has_next():
            from django.core.paginator import EmptyPage
            raise EmptyPage("Page next does not exist.")
        return self.number + 1


class _CachedPaginator:
    """Minimal stand-in for ``django.core.paginator.Paginator``.

    The template uses ``paginator.count``, ``num_pages``, ``page_range`` and
    ``per_page`` — nothing that requires a real queryset or list.
    """

    def __init__(self, count, per_page):
        self.count = count
        self.per_page = per_page

    @property
    def num_pages(self):
        if self.count == 0:
            return 1
        return (self.count + self.per_page - 1) // self.per_page

    @property
    def page_range(self):
        return range(1, self.num_pages + 1)


def _product_to_dict(product):
    """Serialize the subset of Product fields the list template reads.

    Storing dicts (not model instances) in the cache avoids stale-model
    issues: the cached data is a snapshot that renders correctly even after
    transaction rollbacks, and it survives across processes without DB hits
    on a cache hit.
    """
    return {
        'id': product.id,
        'name': product.name,
        'slug': product.slug,
        'price': str(product.price),
        'image_url': product.image.url if product.image else None,
    }


def _cached_product_to_obj(d):
    """Wrap a cached product dict in a lightweight object the template can use.

    The template accesses ``product.name``, ``product.price``,
    ``product.get_absolute_url`` (via slug), and ``product.image.url``.
    """
    return _ProductProxy(
        id=d['id'],
        name=d['name'],
        slug=d['slug'],
        price=d['price'],
        image_url=d['image_url'],
    )


class _ProductProxy:
    """Minimal product stand-in for cached list rendering.

    Provides the attributes/methods the list template accesses: ``name``,
    ``price``, ``get_absolute_url``, ``image`` (with a ``url`` attr).
    """

    def __init__(self, id, name, slug, price, image_url):
        self.id = id
        self.name = name
        self.slug = slug
        self.price = price
        self.image_url = image_url

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('shop:product_detail', args=[self.id, self.slug])

    @property
    def image(self):
        class _Image:
            def __init__(self, url):
                self.url = url
        return _Image(self.image_url) if self.image_url else None


def _cached_categories():
    """Return the global category list, fetched fresh on cache miss.

    Categories are fetched with ``only('id', 'name', 'slug')`` because the
    template only needs those fields (no description/price lookups).
    """
    return list(Category.objects.only('id', 'name', 'slug'))


def product_list(request, category_slug=None):
    # --- Categories (global, longer TTL) ---
    categories = cache.get(cache_key_categories())
    if categories is None:
        categories = _cached_categories()
        cache.set(cache_key_categories(), categories, CATEGORIES_TTL)

    # --- Listing + facet cache keys ---
    query = request.GET.get('q', '').strip()
    selection = parse_facets(request.GET)
    page_number = request.GET.get('page') or 1

    # Build base queryset (same logic as before, but we may use cached data).
    products = Product.objects.filter(available=True)
    if query:
        products = search_products(products, query)
    category = None
    if category_slug:
        category = get_object_or_404(Category, slug=category_slug)
        products = products.filter(category=category)

    base_products = apply_rating_annotation(products)
    if selection.is_active():
        products = apply_facets(base_products, selection)

    # Compute cache keys from the request parameters.
    products_key = cache_key_products(category_slug, query, selection, page_number)
    facets_key = cache_key_facets(category_slug, query, selection, page_number)

    # --- Cached facet counts ---
    counts = cache.get(facets_key, _CACHE_MISS)
    if counts is _CACHE_MISS:
        counts = facet_counts(base_products, selection)
        register_cache_key(facets_key)
        cache.set(facets_key, counts, PRODUCTS_TTL)

    # --- Cached paginated listing ---
    # We cache the serialized product dicts for this page + paginator
    # metadata (total count, page number). On a cache hit we reconstruct a
    # page-like object from the cached dicts — no DB query needed.
    cached = cache.get(products_key, _CACHE_MISS)
    if cached is not _CACHE_MISS:
        product_dicts, total_count, cached_page_number = cached
        product_list_data = [_cached_product_to_obj(d) for d in product_dicts]
        page_obj = _CachedPage(
            object_list=product_list_data,
            number=cached_page_number,
            count=total_count,
            per_page=PRODUCTS_PER_PAGE,
        )
    else:
        # Pagination (card d8haf54c): 12 products per page. An invalid page
        # number (non-integer, out of range, or empty) falls back to page 1;
        # an empty page redirects to the last valid page via the template
        # controls rather than raising. Query params (``q``, facets) ride along
        # on every page link so the filters and search stay applied as the user
        # pages through results. An explicit ordering keeps pagination
        # deterministic when search isn't ranking results (avoids
        # UnorderedObjectListWarning from the rating annotation).
        paginated_qs = products if query else products.order_by('name')
        paginator = Paginator(paginated_qs, PRODUCTS_PER_PAGE)
        page_obj = paginator.get_page(page_number)

        # Cache the serialized products for this page + paginator metadata.
        cached_value = (
            [_product_to_dict(p) for p in page_obj.object_list],
            paginator.count,
            page_obj.number,
        )
        register_cache_key(products_key)
        cache.set(products_key, cached_value, PRODUCTS_TTL)

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
