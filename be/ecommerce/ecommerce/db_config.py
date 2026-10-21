"""Database configuration helpers.

Card g89b45f5 — Phase 1.1 (critical security): default Postgres
credentials were removed. Any Postgres configuration must carry an
explicit password from the environment; there is no fallback.

Card a8g83e7c — connection pooling via CONN_MAX_AGE / CONN_HEALTH_CHECKS.

Card 79e887g9 — optional read replicas via DB_REPLICA_URLS.
"""
from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured

__all__ = [
    'postgres_database',
    'parse_replica_urls',
]

_DEFAULT_USER = 'ecommerce'
_DEFAULT_HOST = 'localhost'
_DEFAULT_PORT = '5432'


def _apply_conn_max_age(config: dict, env: Mapping[str, str]) -> dict:
    """Apply the DB_CONN_MAX_AGE pooling truth table to *config* in place.

    This is the single source of truth for persistent-connection behaviour,
    shared by the primary ``postgres_database`` and by replica aliases so the
    pooling logic is never duplicated:

    - unset/empty → no new keys added (byte-identical to historical behaviour)
    - "0"          → CONN_MAX_AGE=0, no CONN_HEALTH_CHECKS
    - positive int → CONN_MAX_AGE=that, CONN_HEALTH_CHECKS=True
    - negative     → ImproperlyConfigured
    - non-integer  → ImproperlyConfigured
    """
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
        'USER': env.get('POSTGRES_USER', _DEFAULT_USER),
        'PASSWORD': password,
        'HOST': env.get('POSTGRES_HOST', _DEFAULT_HOST),
        'PORT': env.get('POSTGRES_PORT', _DEFAULT_PORT),
    }

    return _apply_conn_max_age(config, env)


def _parse_replica_url(url: str) -> dict:
    """Parse a single ``postgres://user:pass@host:port/dbname`` URL into a
    Django database config dict (minus pooling keys).

    Blank/empty segments are already stripped by the caller. A malformed URL
    — one missing required components — raises ``ImproperlyConfigured``.
    """
    parts = urlsplit(url.strip())
    if parts.scheme not in ('postgres', 'postgresql'):
        raise ImproperlyConfigured(
            f'Invalid replica URL: {url!r}. Expected a postgres:// URL.'
        )

    # urlsplit puts host:port in .netloc and the password-less userinfo in .username.
    # The path component is the database name (with a leading slash).
    host = parts.hostname or ''
    port = str(parts.port) if parts.port is not None else '5432'

    if not host:
        raise ImproperlyConfigured(
            f'Invalid replica URL: {url!r}. Missing hostname.'
        )

    # Username / password: prefer the .username/.password attrs (which handle
    # percent-decoding), but fall back to splitting .netloc for older patterns.
    user = parts.username or ''
    password = parts.password or ''

    db_name = parts.path.lstrip('/')

    config: dict = {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': db_name,
        'USER': user,
        'PASSWORD': password,
        'HOST': host,
        'PORT': port,
    }
    return config


def parse_replica_urls(env: Mapping[str, str]) -> dict[str, dict]:
    """Build ``{alias: config}`` for read replicas from ``DB_REPLICA_URLS``.

    Truth table — when the env var is unset, empty, or contains only blank
    segments, returns an empty dict (zero replicas). ``settings.py`` treats an
    empty result as "no routing": no ``DATABASE_ROUTERS``, no new aliases.

    - ``DB_REPLICA_URLS=postgres://user:pw@replica1:5432/shop,postgres://user:pw@replica2:5432/shop``
      → ``{'replica_0': {...}, 'replica_1': {...}}``

    Blank segments between commas are ignored. A segment that does not parse
    as a postgres URL raises ``ImproperlyConfigured``.
    """
    raw = env.get('DB_REPLICA_URLS', '') or ''
    urls = [seg.strip() for seg in raw.split(',') if seg.strip()]
    if not urls:
        return {}

    replicas: dict[str, dict] = {}
    for i, url in enumerate(urls):
        alias = f'replica_{i}'
        config = _parse_replica_url(url)
        _apply_conn_max_age(config, env)
        replicas[alias] = config

    return replicas
