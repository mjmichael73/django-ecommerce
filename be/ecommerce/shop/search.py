"""Full-text search (card bf247ef9).

``search_products`` is the single entry point the catalog list view uses to
turn a user query into a Product queryset.

Design (binding, from the card brief):

* PostgreSQL-native, on-the-fly vectors: ``SearchVector('name', weight='A')
  + SearchVector('description', weight='B')`` is computed per query — no
  persisted ``SearchVectorField``, no trigger, no migration, no DB
  extension prerequisite. The scaling path (persisted vector column + GIN
  index) is a documented non-goal of this card (docs/architecture.md).
* Config ``'english'`` (stemming); a plain ``SearchQuery`` — websearch
  operators (``-exclude``, OR, quoted phrases) are a documented non-goal.
* Only ``available=True`` products are ever returned; search must not leak
  unlisted products.
* An empty/whitespace query is not an error: it means "no filtering", so the
  plain catalog page and the search page are the same view.
* Results are ordered ``-rank, name`` so name matches outrank
  description-only matches and ordering is deterministic.

CI runs the suite on SQLite (no Postgres service), so on any non-Postgres
engine the same call degrades to an ``icontains`` OR over the same two
fields. The fallback is CI-only scaffolding, not a product surface — it has
no stemming and no ranking, and it keeps the *contract* tests (results,
availability filter, empty-query passthrough) green on every backend.
"""
from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.db import connection
from django.db.models import Q

_SEARCH_CONFIG = 'english'


def search_products(queryset, query):
    """Filter ``queryset`` (of Product) by ``query``, best matches first.

    An empty/whitespace query returns the queryset unchanged (model ordering
    applies). With a query, results are filtered, annotated with ``rank``
    and ordered ``-rank, name``.
    """
    if not query or not query.strip():
        return queryset

    if connection.vendor != 'postgresql':
        # SQLite (CI) fallback: substring OR over the same fields the
        # Postgres path searches. No stemming/ranking — contract parity only.
        condition = Q()
        for term in query.split():
            condition |= Q(name__icontains=term) | Q(description__icontains=term)
        return queryset.filter(condition)

    vector = (
        SearchVector('name', weight='A')
        + SearchVector('description', weight='B')
    )
    search_query = SearchQuery(query, config=_SEARCH_CONFIG)
    return (
        queryset
        .annotate(search=vector, rank=SearchRank(vector, search_query))
        .filter(search=search_query)
        .order_by('-rank', 'name')
    )


__all__ = ['search_products']
