"""Full-text search (card bf247ef9).

``search_products`` is the single entry point the catalog list view uses to
turn a user query into a Product queryset.

Design (binding, from the card brief):

* PostgreSQL full-text search when the default database is Postgres: a
  ``search_rank`` expression built from the trigger-maintained
  ``search_vector`` column (see ``shop/migrations/0005_product_search_vector``),
  weighted name (A) > description (B).
* Any other engine (the project's SQLite fallback) degrades to an
  ``icontains`` OR over the same two fields — same results surface, no
  Postgres-specific machinery required.
* Only ``available=True`` products are ever returned; search must not leak
  unlisted products.
* An empty/whitespace query is not an error: it means "no filtering", so the
  plain catalog page and the search page are the same view.
* Multi-term queries are ORed per whitespace-separated term — plain
  ``SearchQuery`` would AND the terms and hide every product matching only
  some of them. Ranking pushes products that match more (and stronger) terms
  to the top; ties break on relevance first, then the model default
  ``ordering = ['name']``.

The vector column is maintained by a trigger inside the migration, so no
application code path can forget to refresh it (saves, admin edits, loads all
update the index without extra queries). ``search_vector.update_from...`` is
deliberately NOT wired into ``Product.save()`` — the trigger owns the column.
"""
from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db import connection
from django.db.models import F, Q

# Weight order is A, B, C, D — position 0 is weight A (highest). These must
# mirror the setweight() calls in migration 0005 (name A, description B).
_NAME_WEIGHT = 0.6
_DESCRIPTION_WEIGHT = 0.1

# Same text-search configuration the migration's trigger and backfill use to
# build the stored vectors; queries must be parsed with the same config so
# stemming and stopword removal agree with the index.
_SEARCH_CONFIG = 'pg_catalog.english'


def _is_postgres():
    return connection.vendor == 'postgresql'


def _lexeme_terms(terms):
    """Return the terms that yield at least one lexeme under ``_SEARCH_CONFIG``.

    A term that reduces to no lexemes (stopwords like "or", punctuation-only
    tokens) would become an empty tsquery operand. Standalone that matches
    nothing, but inside ``||`` an empty operand makes the whole disjunction
    match *every* row — so such terms must be dropped before combining, or
    "tea OR mug" would return the entire catalog. One roundtrip per request:
    ``plainto_tsquery`` per term in a single ``SELECT`` list — the same
    function ``SearchQuery(search_type='plain')`` compiles to, so the filter
    agrees with the query builder.
    """
    if not terms:
        return []
    placeholders = ', '.join(['plainto_tsquery(%s, %s)'] * len(terms))
    params = []
    for term in terms:
        params.extend([_SEARCH_CONFIG, term])
    with connection.cursor() as cursor:
        cursor.execute('SELECT ' + placeholders, params)
        row = cursor.fetchone()
    return [term for term, tsq in zip(terms, row) if tsq]


def search_products(queryset, query):
    """Filter ``queryset`` (of Product) by ``query``, best matches first.

    An empty query returns the queryset unchanged (model ordering applies).
    """
    if not query or not query.strip():
        return queryset

    if not _is_postgres():
        # SQLite fallback: substring match over the same fields the Postgres
        # path indexes. Case-insensitivity comes from SQLite LIKE semantics;
        # terms are ORed like the Postgres path.
        condition = Q()
        for term in query.split():
            condition |= Q(name__icontains=term) | Q(description__icontains=term)
        return queryset.filter(condition)

    terms = _lexeme_terms(query.split())
    if not terms:
        # Nothing searchable left (stopwords / punctuation only): no product
        # can match, so return none rather than an unfiltered catalog.
        return queryset.none()

    search_query = SearchQuery(terms[0], config=_SEARCH_CONFIG)
    for term in terms[1:]:
        search_query |= SearchQuery(term, config=_SEARCH_CONFIG)
    vector = F('search_vector')
    rank = SearchRank(
        vector,
        search_query,
        weights=[_NAME_WEIGHT, _DESCRIPTION_WEIGHT, 0.1, 0.1],
    )
    return (
        queryset
        .filter(search_vector=search_query)
        .annotate(search_rank=rank)
        .order_by('-search_rank', 'name')
    )


__all__ = ['search_products']
