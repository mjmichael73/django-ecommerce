"""Database router for primary + read replica topology (card 79e887g9).

The router implementation lives in :mod:`ecommerce.db_config`
(``PrimaryReplicaRouter``) so the alias list is constructed right next to the
config it routes to. This module re-exports the public class so the conventional
import path ``from ecommerce.db_router import PrimaryReplicaRouter`` works, and
settings.py can write ``DATABASE_ROUTERS = ['ecommerce.db_router.PrimaryReplicaRouter']``.
"""
from ecommerce.db_config import PrimaryReplicaRouter  # noqa: F401
