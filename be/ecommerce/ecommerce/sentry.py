"""Sentry SDK initialisation for Django + Celery (card 5ggb4929).

Sentry is entirely optional: when ``SENTRY_DSN`` is unset or ``sentry-sdk`` is
not installed, importing this module is a harmless no-op and
:func:`init_sentry` does nothing. It must be safe to call at any point
during startup (wsgi/asgi entry, celery app bootstrap, management commands).

The wiring decision lives in :mod:`ecommerce.sentry_config` so settings and
tests can share the exact same env-parsing logic without importing the SDK.

Sensitive-data handling (card 5ggb4929 — prevent auth/payment/request-data
leakage into Sentry):

  * ``send_default_pii=False`` by default — Django's CSRF cookie value,
    session key, and auth state are **not** auto-attached to events.
  * Sentry's automatic request-body scrubbing strips the sensitive HTTP
    headers named in :data:`~ecommerce.sentry_config.SENSITIVE_HTTP_HEADERS`
    (authorization, cookie, set-cookie, x-csrftoken) and their Cookie
    sub-values.
  * A custom :class:`SentryScrubbingProcessor` redacts payment/credential
    field names (card numbers, CVC, expiry, Stripe secret keys, passwords,
    tokens, etc.) from the structured ``extra`` / request-body / form data
    Sentry captures, matching the keys already redacted by the project's JSON
    log formatter (see ``ecommerce/logging.py`` ``SENSITIVE_SUFFIXES``).
  * Cookie values are redacted from ``request.cookies``; only non-auth cookies
    (e.g. ``preferences=theme=dark``) are preserved.
  * Stripe ``idempotency_key`` and ``client_reference_id`` are safe
    (non-sensitive) reference tokens — they are kept so refund/replay
    correlation still works in Sentry.

Scrubbing is **fail-safe**: the processor always redacts sensitive keys
unless an operator explicitly disables scrubbing via ``SENTRY_SCRUB_PAYMENT_DATA=0``.
Even when scrubbing is disabled, safe Stripe correlation tokens are always preserved.
"""
from __future__ import annotations

import logging
import os

from ecommerce.sentry_config import (
    SENSITIVE_COOKIE_PATTERNS,
    SENSITIVE_PAYMENT_FIELDS,
    SentryConfig,
    sentry_config as _build_config,
)

logger = logging.getLogger(__name__)

# Namespaced under this module so ``init_sentry`` is idempotent across the
# WSGI entry, the Celery bootstrap, and re-imports.
_initialised: bool = False


class SentryScrubbingProcessor:
    """Event processor that redacts sensitive field *values* from Sentry events.

    Plumbed into ``sentry_sdk.init(before_send=...)`` so it runs on every
    event before transmission. It walks the JSON-serialisable event dict
    (request data, extra, contexts, user) and replaces values whose key is
    sensitive, matching the project's existing ``SENSITIVE_SUFFIXES`` policy
    from the JSON log formatter.

    Keys that refer to Stripe ``idempotency_key`` / ``client_reference_id``
    are explicitly *kept* — they are non-sensitive correlation tokens, not
    credentials, and dropping them would break refund/replay tracing.

    The processor is **fail-safe**: when constructed without a config (or with
    a config whose scrub flags are unset/default), it always scrubs payment
    data and cookies. The scrub flags on :class:`~ecommerce.sentry_config.SentryConfig`
    are respected only when explicitly provided, so an operator can opt out
    of payment scrubbing if (and only if) they have a specific operational
    reason and understand the PII implications.
    """

    # Bare key names (no path) that are non-sensitive Stripe correlation
    # tokens — keep these so Sentry can correlate webhook replays / payouts.
    _SAFE_STRIPE_KEYS = frozenset({
        'idempotency_key',
        'client_reference_id',
        'order_id',
        'request_id',
    })

    def __init__(self, cfg: SentryConfig | None = None) -> None:
        """Optionally bind a :class:`SentryConfig` to honour its scrub flags.

        Without a config the processor defaults to full scrubbing (fail-safe).
        """
        self._scrub_payment = cfg.scrub_payment_data if cfg else True
        self._scrub_cookie_values = cfg.scrub_request_cookies if cfg else True

    def __call__(self, event: dict, hint: dict | None = None) -> dict | None:
        """Mutate *event* in place, redacting sensitive values.

        Returns the (mutated) event so Sentry continues processing it; a
        ``None`` return would silently drop the event, which we never do.
        """
        if not event:
            return event
        self._scrub_dict(event, scrub_payment=self._scrub_payment)
        # Sentry stores request data under ``request``. Redact its parsed
        # ``data`` (form/JSON body) and ``cookies`` sub-dicts explicitly so
        # cookie-name sensitivity (sessionid/csrftoken) is handled, not just
        # field-suffix matching.
        request = event.get('request')
        if isinstance(request, dict):
            self._scrub_dict(request, scrub_payment=self._scrub_payment)
            data = request.get('data')
            if isinstance(data, dict):
                self._scrub_dict(data, scrub_payment=self._scrub_payment)
            cookies = request.get('cookies')
            if isinstance(cookies, dict) and self._scrub_cookie_values:
                self._scrub_cookies(cookies)
        # User context, tags, extra, contexts — walk them all defensively.
        for key in ('user', 'extra', 'contexts', 'tags'):
            node = event.get(key)
            if isinstance(node, dict):
                self._scrub_dict(node, scrub_payment=self._scrub_payment)
            elif isinstance(node, (list, tuple)):
                for item in node:
                    if isinstance(item, dict):
                        self._scrub_dict(item, scrub_payment=self._scrub_payment)
        return event

    # ------------------------------------------------------------------ #
    # Internal: recursive scrubbing
    # ------------------------------------------------------------------ #

    def _scrub_dict(self, obj: dict, scrub_payment: bool = True) -> None:
        """Redact values in *obj* whose key looks sensitive (in place).

        ``scrub_payment`` controls whether payment/credential fields are
        redacted — defaults to ``True`` (fail-safe) when the caller has no
        config. Safe Stripe correlation tokens are always preserved regardless.
        """
        if not isinstance(obj, dict):
            return
        for key in list(obj):
            value = obj[key]
            lowered = str(key).lower()
            if lowered in self._SAFE_STRIPE_KEYS:
                # Keep — non-sensitive Stripe correlation token.
                continue
            if scrub_payment and _is_sensitive_key(lowered):
                obj[key] = '***REDACTED***'
                continue
            if isinstance(value, dict):
                self._scrub_dict(value, scrub_payment=scrub_payment)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    if isinstance(item, dict):
                        self._scrub_dict(item, scrub_payment=scrub_payment)

    def _scrub_cookies(self, cookies: dict) -> None:
        """Redact cookie *values* whose name carries session/auth material.

        Sentry passes ``request.cookies`` as a plain ``{name: value}`` dict.
        Cookie names like ``sessionid``/``csrftoken``/``*token`` carry identity
        material whose value must never reach Sentry; non-auth cookies (e.g.
        ``preferences=theme=dark``) are preserved.
        """
        for name in list(cookies):
            if _is_sensitive_cookie_name(name):
                cookies[name] = '***REDACTED***'


def _is_sensitive_key(key: str) -> bool:
    """True when *key* (a dotted path or bare name) is sensitive.

    Suffix-match (e.g. ``user.password``), whole-key match (e.g. ``password``),
    and the shared payment/credential set from ``sentry_config``.
    """
    lowered = key.lower()
    # Suffix match: ``user.password``, ``card.cvc``, ``request_id`` safe above.
    for suffix in SENSITIVE_PAYMENT_FIELDS:
        if lowered == suffix or lowered.endswith('.' + suffix) or lowered.endswith(suffix):
            return True
    return False


def _is_sensitive_cookie_name(name: str) -> bool:
    """True when a cookie name carries session identity / auth material.

    Sentry's ``before_send`` receives the already-parsed ``request.cookies``
    dict; we redact values whose *name* matches a known sensitive pattern.
    """
    lower = name.lower()
    return any(pattern in lower for pattern in SENSITIVE_COOKIE_PATTERNS)


def _build_sentry_options(cfg: SentryConfig) -> dict:
    """Translate a :class:`SentryConfig` into the ``sentry_sdk.init`` kwargs."""
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.django import DjangoIntegration

    integrations = []
    if cfg.django_enabled:
        integrations.append(DjangoIntegration())
    if cfg.celery_enabled:
        integrations.append(CeleryIntegration())

    scrubber = SentryScrubbingProcessor(cfg)
    options: dict = {
        'dsn': cfg.dsn,
        'environment': cfg.environment,
        'release': cfg.release,
        'traces_sample_rate': cfg.traces_sample_rate,
        'send_default_pii': cfg.send_default_pii,
        'before_send': scrubber,
        'before_breadcrumb': scrubber,
        'request_bodies': 'always',  # we scrub sensitive fields ourselves
    }
    if integrations:
        options['integrations'] = integrations
    # Profiles sampling is opt-in (0.0 default = off).
    if cfg.profiles_sample_rate > 0:
        options['profiles_sample_rate'] = cfg.profiles_sample_rate
    return options


def init_sentry() -> bool:
    """Initialise Sentry from the environment, if configured.

    Returns ``True`` when Sentry was initialised, ``False`` when skipped
    (DSN unset, SDK not installed, or already initialised). This is safe to
    call repeatedly — subsequent calls are no-ops.

    The SDK is imported *lazily* inside the try/except so a missing
    ``sentry-sdk`` package never breaks application startup or tests.
    ``sentry-sdk`` is pinned in requirements.txt and always present in the
    image; it is imported lazily so the SDK is only activated when ``SENTRY_DSN``
    is set.
    """
    global _initialised

    cfg = _build_config(os.environ)
    if cfg is None:
        # DSN unset — Sentry off. This is the test/default path.
        return False
    if _initialised:
        # Already initialised in this process — idempotent.
        return True

    try:
        import sentry_sdk  # type: ignore[import-untyped]
    except ImportError:
        logger.warning(
            'SENTRY_DSN is set but sentry-sdk is not installed; '
            'install with `pip install sentry-sdk` to enable error tracking.'
        )
        return False

    options = _build_sentry_options(cfg)
    sentry_sdk.init(**options)
    _initialised = True
    logger.info(
        'Sentry initialised (environment=%s, release=%s, traces_sample_rate=%s)',
        cfg.environment, cfg.release, cfg.traces_sample_rate,
    )
    return True


def reset_sentry_for_tests() -> None:
    """Reset the initialised flag (test helper / module reload).

    Tests that want to re-assert the initialisation path (e.g. that the SDK is
    called exactly once) can call this to reset the module-level guard.
    """
    global _initialised
    _initialised = False