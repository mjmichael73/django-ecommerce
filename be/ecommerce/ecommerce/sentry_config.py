"""Sentry error-tracking configuration (card 5ggb4929 — Phase 4.1 monitoring).

Pure-python helper — same pattern as ``db_config`` / ``cache_config`` /
``social_auth_config``: computes from environment variables only, no Django
imports, so settings and tests can share the exact same wiring decision.

Rule (card g89b45f5 discipline, no credentials in the repo): Sentry is fully
wired only when ``SENTRY_DSN`` is set and non-empty. When unset/empty, no
Sentry SDK is initialised — startup, request handling and Celery are entirely
unaffected. ``sentry-sdk`` is pinned in requirements.txt (so the image always
has it), but it is imported lazily by ``init_sentry()`` only when a DSN is
present, so a missing or unset DSN never causes an SDK import.

Sensitive-data scrubbing:
  * ``SENTRY_SCRUB_REQUEST_HEADERS`` (default ``true``): scrub the standard
    sensitive headers (authorization, cookie, set-cookie) from the request
    data Sentry captures automatically.
  * ``SENTRY_SCRUB_REQUEST_COOKIES`` (default ``true``): scrub Cookie headers
    (which carry the session id and CSRF token) from Sentry events.
  * ``SENTRY_SCRUB_PAYMENT_DATA`` (default ``true``): strip Stripe/PayPal-style
    payment fields (card number, CVC, expiry, secret keys) from request
    bodies, form data, and JSON payloads before they reach Sentry. This is
    defence-in-depth on top of Django's own redaction in the JSON log
    formatter (see ``ecommerce/logging.py`` ``SENSITIVE_SUFFIXES``).
  * ``SENTRY_SENSITIVE_FIELDS`` (default: the union of logging's
    ``SENSITIVE_SUFFIXES``): additional dotted-path / suffix keys to scrub from
    the structured event data Sentry captures.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

__all__ = [
    'sentry_config',
    'SentryConfig',
    'is_sentry_enabled',
    'SENSITIVE_HTTP_HEADERS',
    'SENSITIVE_COOKIE_PATTERNS',
    'SENSITIVE_PAYMENT_FIELDS',
    'SENTRY_DSN_ENV',
]

logger = logging.getLogger(__name__)

SENTRY_DSN_ENV = 'SENTRY_DSN'

# HTTP headers considered sensitive — scrubbed from Sentry's automatic request
# data capture when SENTRY_SCRUB_REQUEST_HEADERS is on.
SENSITIVE_HTTP_HEADERS = frozenset({
    'authorization',
    'cookie',
    'set-cookie',
    'x-csrftoken',
})

# Cookie-name substrings that carry session identity or auth material and must
# never appear in a Sentry event.
SENSITIVE_COOKIE_PATTERNS = (
    'sessionid',
    'csrftoken',
    'token',
    'secret',
)

# Payment / credential field names (compared case-insensitively as a *suffix*
# of a dotted path, matching the logging module's convention) that are stripped
# from every Sentry event before transmission.
SENSITIVE_PAYMENT_FIELDS = frozenset({
    'card', 'cardnumber', 'card_number', 'number',
    'cvc', 'cvv', 'csc',
    'exp', 'expiry', 'exp_month', 'exp_year', 'expiration',
    'cctype', 'cardtype',
    'stripe_secret_key', 'stripe_publishable_key', 'secret_key',
    'stripe_id', 'payment_intent', 'charge',
    'webhook_secret', 'stripe_webhook_secret',
    'google_client_secret', 'google_client_id',
    'password', 'passwd', 'secret', 'token', 'apikey', 'api_key',
    'access_token', 'refresh_token', 'authorization',
    'card_cvc', 'card_cvv',
    'postal_code', 'address', 'city',
})


def _env_bool(name: str, default: bool) -> bool:
    """Read a boolean env var; empty/unset returns the *default*."""
    raw = os.environ.get(name)
    if raw is None or raw == '':
        return default
    return raw.lower() in ('1', 'true', 'yes')


@dataclass(frozen=True)
class SentryConfig:
    """Configuration describing how Sentry is wired, or ``None`` when off.

    ``options`` is the keyword dict handed verbatim to
    ``sentry_sdk.init()``. It is built lazily by :func:`sentry_config` so the
    SDK is only configured once, at application start.
    """

    dsn: str
    environment: str
    release: str
    traces_sample_rate: float
    profiles_sample_rate: float
    send_default_pii: bool
    scrub_request_headers: bool
    scrub_request_cookies: bool
    scrub_payment_data: bool
    sensitive_fields: tuple
    celery_enabled: bool
    django_enabled: bool
    options: dict = field(default_factory=dict, repr=False)


def _sensitive_field_tuple() -> tuple:
    """Build the sensitive-fields tuple from env + the payment/default sets.

    ``SENTRY_SENSITIVE_FIELDS`` (comma-separated) extends — not replaces — the
    built-in default set, so callers can add custom fields without losing the
    payment/card scrubbing that protects PII by default.
    """
    extra_raw = os.environ.get('SENTRY_SENSITIVE_FIELDS', '')
    extra = frozenset(
        f.strip().lower() for f in extra_raw.split(',') if f.strip()
    )
    # Merge with the payment/credential defaults so payment data is always
    # scrubbed unless the operator explicitly opts out of scrubbing entirely.
    base = set(SENSITIVE_PAYMENT_FIELDS)
    base.update(extra)
    return tuple(sorted(base))


def _env_float(name: str, default: float) -> float:
    """Read a float env var; empty/unset/ invalid returns *default*."""
    raw = os.environ.get(name)
    if raw is None or raw == '':
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def sentry_config(env: dict[str, str] | os._Environ) -> SentryConfig | None:
    """Build the Sentry wiring from *env*, or ``None`` when disabled.

    ``env`` maps variable names to strings (normally ``os.environ``).

    The provider is fully wired only when ``SENTRY_DSN`` is set and non-empty.
    When disabled, this returns ``None`` so callers can short-circuit without
    importing ``sentry_sdk``.

    ``sentry-sdk`` itself is an *optional* dependency. This helper never
    imports it — ``sentry_sdk.init()`` is invoked by
    :func:`ecommerce.sentry.init_sentry` only when present and configured, so a
    missing package never breaks startup.
    """
    dsn = (env.get(SENTRY_DSN_ENV) or '').strip()
    if not dsn:
        return None

    return SentryConfig(
        dsn=dsn,
        environment=(env.get('SENTRY_ENVIRONMENT') or '').strip() or 'unknown',
        release=(env.get('SENTRY_RELEASE') or '').strip()
        or _detect_release(env),
        traces_sample_rate=_env_float('SENTRY_TRACES_SAMPLE_RATE', 0.2),
        profiles_sample_rate=_env_float('SENTRY_PROFILES_SAMPLE_RATE', 0.0),
        send_default_pii=_env_bool('SENTRY_SEND_DEFAULT_PII', False),
        scrub_request_headers=_env_bool('SENTRY_SCRUB_REQUEST_HEADERS', True),
        scrub_request_cookies=_env_bool('SENTRY_SCRUB_REQUEST_COOKIES', True),
        scrub_payment_data=_env_bool('SENTRY_SCRUB_PAYMENT_DATA', True),
        sensitive_fields=_sensitive_field_tuple(),
        celery_enabled=_env_bool('SENTRY_CELERY_ENABLED', True),
        django_enabled=_env_bool('SENTRY_DJANGO_ENABLED', True),
    )


def _detect_release(env: dict[str, str]) -> str:
    """Best-effort release string for the Sentry ``release`` option.

    Prefers an explicit ``SENTRY_RELEASE`` (handled by the caller); otherwise
    derives one from ``GIT_COMMIT`` / ``GIT_SHA`` / ``APP_VERSION`` / a static
    marker. The value is never security-sensitive — if nothing is available we
    fall back to ``'django-ecommerce@unreleased'`` so releases are still
    distinguishable in the Sentry UI.
    """
    for key in ('GIT_COMMIT', 'GIT_SHA', 'APP_VERSION', 'VERSION'):
        val = (env.get(key) or '').strip()
        if val:
            return val
    return 'django-ecommerce@unreleased'


def is_sentry_enabled() -> bool:
    """True when ``SENTRY_DSN`` is set and non-empty in the active env.

    Reads the env at call time (not import time) so ``override_settings`` +
    env patching in tests is honoured. The companion :func:`sentry_config`
    caches nothing — it rebuilds the config each call, which is cheap and
    avoids stale module state across re-imports / test reloads.
    """
    return bool(os.environ.get(SENTRY_DSN_ENV, '').strip())
