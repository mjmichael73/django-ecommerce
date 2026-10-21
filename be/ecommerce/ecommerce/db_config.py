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
    config: dict = {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': env.get('POSTGRES_DB', ''),
        'USER': env.get('POSTGRES_USER', 'ecommerce'),
        'PASSWORD': password,
        'HOST': env.get('POSTGRES_HOST', 'localhost'),
        'PORT': env.get('POSTGRES_PORT', '5432'),
    }

    # Native Django persistent-connection support (card a8g83e7c).
    # DB_CONN_MAX_AGE maps to Django's CONN_MAX_AGE (seconds to keep a
    # connection open across requests). When unset/empty the dict above is
    # returned unchanged — byte-identical to the historical behaviour.
    raw_max_age = env.get('DB_CONN_MAX_AGE', '')
    if not raw_max_age:
        # Unset or empty → no new keys, default behaviour preserved.
        return config

    try:
        max_age = int(raw_max_age)
    except (TypeError, ValueError):
        raise ImproperlyConfigured(
            'DB_CONN_MAX_AGE must be an integer (seconds). '
            'Use 0 to disable persistent connections (Django default), '
            'or a positive value (e.g. 60) to enable them.'
        )

    if max_age < 0:
        raise ImproperlyConfigured(
            'DB_CONN_MAX_AGE must not be negative.'
        )

    config['CONN_MAX_AGE'] = max_age
    if max_age > 0:
        # CONN_HEALTH_CHECKS only makes sense when connections are reused
        # (CONN_MAX_AGE > 0); with 0 there is nothing to health-check.
        config['CONN_HEALTH_CHECKS'] = True

    return config
