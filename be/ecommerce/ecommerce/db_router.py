"""Database router for primary + read replica topology (card 79e887g9).

The ``DB_REPLICA_URLS`` env var is parsed in :mod:`ecommerce.db_config`
(``parse_replica_urls``); this module owns the router class that settings.py
installs via ``DATABASE_ROUTERS = ['ecommerce.db_router.PrimaryReplicaRouter']``
when replicas are configured.

Routing policy:
- ``db_for_read``   → a random configured replica, or ``None`` (→ default)
  when no replicas are configured (router is inert).
- ``db_for_write``  → ``None`` (always ``default`` — replicas are read-only).
- ``allow_relation``→ ``True`` for default↔replica pairs (same dataset);
  ``None`` otherwise so Django's default applies.
- ``allow_migrate`` → ``True`` only for ``default``; migrations never run
  on replicas.
"""
from __future__ import annotations

import random
from collections.abc import Mapping


class PrimaryReplicaRouter:
    """Route reads to a random replica; all writes/relations/migrations → default.

    When no replicas are configured, every method is inert: reads fall back to
    ``'default'`` (``None`` signals Django to use the default database), so the
    router can always sit in ``DATABASE_ROUTERS`` without harm — but settings.py
    only installs the router when replicas are actually configured.
    """

    def __init__(self, aliases: Mapping[str, dict] | None = None):
        self._alias_list: list[str] = (
            list(aliases) if aliases else []
        )

    def db_for_read(self, model, **hints) -> str | None:
        if self._alias_list:
            return random.choice(self._alias_list)
        return None  # Django falls back to 'default'

    def db_for_write(self, model, **hints) -> str | None:
        return None  # always 'default'

    def allow_relation(self, obj1, obj2, **hints) -> bool | None:
        """Objects can relate when both are on default/replica aliases."""
        db_set = {'default', *self._alias_list}
        db_obj1 = getattr(obj1, '_state', None)
        db_obj2 = getattr(obj2, '_state', None)
        db1 = getattr(db_obj1, 'db', None)
        db2 = getattr(db_obj2, 'db', None)
        if db1 in db_set and db2 in db_set:
            return True
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints) -> bool:
        """Migrations run only on 'default' — never on replicas."""
        if db == 'default':
            return True
        return False
