"""Database configuration helpers.

Card g89b45f5 — Phase 1.1 (critical security): default Postgres
credentials were removed. Any Postgres configuration must carry an
explicit password from the environment; there is no fallback.
"""
from __future__ import annotations

from collections.abc import Mapping

from django.core.exceptions import ImproperlyConfigured


def postgres_database(env: Mapping[str, str]) -> dict:
    """Build the Django ``DATABASES['default']`` entry for Postgres from *env*.

    Raises ``ImproperlyConfigured`` when ``POSTGRES_PASSWORD`` is missing or
    empty — an empty password would silently fall back to whatever default
    credentials the server accepts.
    """
    password = env.get('POSTGRES_PASSWORD', '')
    if not password:
        raise ImproperlyConfigured(
            'POSTGRES_PASSWORD must be set when Postgres is used '
            '(POSTGRES_DB is set). No default database credentials are '
            'allowed — put a real password in .env (see .env.example).'
        )
    return {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': env.get('POSTGRES_DB', ''),
        'USER': env.get('POSTGRES_USER', 'ecommerce'),
        'PASSWORD': password,
        'HOST': env.get('POSTGRES_HOST', 'localhost'),
        'PORT': env.get('POSTGRES_PORT', '5432'),
    }
