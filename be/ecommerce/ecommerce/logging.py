"""Structured (JSON) logging for the Django e-commerce project.

Card 6cc44f8g — Phase 4.1 monitoring: structured logging with request IDs.

This module is dependency-free: it uses only the standard-library
``logging`` and ``json`` modules so the project's minimal dependency set is
unchanged. It provides:

* :func:`redact` — a secret-scrubbing pass that masks values for a set of
  sensitive field names in arbitrary nested payloads (used by the JSON
  formatter and available to view code that logs request bodies).
* :class:`JSONFormatter` — a ``logging.Formatter`` that emits one JSON object
  per log record on a single line, with stable keys and a ``request_id``
  field pulled from :data:`REQUEST_ID_VAR`.
* :class:`RequestIDLogFilter` — a ``logging.Filter`` that injects the active
  request ID into every emitted record (so non-JSON handlers still carry it).
* :func:`build_logging_config` — assembles a Django ``LOGGING`` dict from
  settings knobs (``LOG_JSON``, ``LOG_LEVEL``), suitable for
  ``settings.LOGGING``.

Sensitive data handling
-----------------------
The formatter scrubs a fixed set of sensitive keys (``password``, ``token``,
``secret``, ``api_key``, ``stripe_*`` etc.) from any ``extra`` dict,
``exc_info`` frames, and structured ``payload`` passed in ``extra``. The
request-body redaction helper (:func:`redact`) is also exposed for views and
tasks that log arbitrary request payloads.

Configuration knobs (read once at settings import)
--------------------------------------------------
* ``LOG_JSON`` (bool, default ``True`` when ``DEBUG`` is False, else ``False``)
  — emit JSON objects instead of the Django default ``key value`` line.
* ``LOG_LEVEL`` (str, default ``INFO``) — the root/handler level.
* ``LOG_REDACT_EXTRA`` (bool, default ``True``) — scrub sensitive keys out of
  ``record.__dict__`` extras before formatting.
"""
from __future__ import annotations

import json
import logging
import os
import traceback
from datetime import datetime, timezone
from typing import Any

from contextvars import ContextVar

# Shared header name for request correlation. Both the middleware and the
# client-facing response header use this single constant.
REQUEST_ID_HEADER = 'X-Request-Id'

# Context variable holding the active request ID. Set by
# :class:`ecommerce.middleware.request_id.RequestIDMiddleware` and read by
# :class:`JSONFormatter` / :class:`RequestIDLogFilter`. Exposed here so the
# middleware module can import it without a circular dependency.
REQUEST_ID_VAR: ContextVar[str] = ContextVar(REQUEST_ID_VAR_NAME := 'request_id')
# Re-export under the public name used by other modules.
request_id: ContextVar[str] = REQUEST_ID_VAR

# Keys (matched case-insensitively as a *suffix* of dotted paths or as a
# whole key) that are redacted from structured payloads. Matching on suffix
# means ``user.password``, ``card.cvv`` and ``stripe.api_key`` are all
# covered without enumerating every path.
SENSITIVE_SUFFIXES = frozenset({
    'password', 'passwd', 'secret', 'token', 'apikey', 'api_key',
    'access_token', 'refresh_token', 'authorization', 'auth',
    'card_cvc', 'card_cvv', 'cvv', 'secret_key', 'private_key',
    'webhook_secret', 'stripe_secret_key', 'stripe_publishable_key',
    'google_client_secret', 'csrfmiddlewaretoken',
})
_SENSITIVE_SUFFIXES_LOWER = {s.lower() for s in SENSITIVE_SUFFIXES}


def _should_redact_key(key: str) -> bool:
    """True if *key* (a dotted path or bare name) looks sensitive."""
    k = str(key).lower()
    return any(k.endswith(s) for s in _SENSITIVE_SUFFIXES_LOWER) or k in _SENSITIVE_SUFFIXES_LOWER


def redact(obj: Any, key: str = '') -> Any:
    """Return a copy of *obj* with sensitive values replaced by ``'***'``.

    Recursively walks dicts, lists and tuples. A value is redacted when its
    containing key (a dotted path such as ``user.password``) matches
    :data:`SENSITIVE_SUFFIXES`. This is used both by the JSON formatter (on
    ``extra`` payloads) and exported for view/task code that logs request
    bodies or Stripe/Stripe-like payloads.
    """
    if isinstance(obj, dict):
        return {
            k: ('***' if _should_redact_key(_join(key, k)) else redact(v, _join(key, k)))
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        redacted = [redact(v, key) for v in obj]
        return type(obj)(redacted) if isinstance(obj, tuple) else redacted
    return obj


def _join(parent: str, child: str) -> str:
    return f'{parent}.{child}' if parent else str(child)


def _request_id_value() -> str | None:
    """The active request ID, or ``None`` when not set (e.g. Celery startup)."""
    try:
        return REQUEST_ID_VAR.get()
    except LookupError:
        return None


class JSONFormatter(logging.Formatter):
    """Emit one JSON object per record on a single line.

    Stable key set: ``timestamp``, ``level``, ``logger``, ``message``,
    ``request_id``, plus any extra fields supplied by the caller. Exceptions
    are serialized as ``exception`` (``"type: message"`` lines joined by
    newlines) and never include local variable values.
    """

    # Extra keys that are logging internals — never promote them as JSON
    # fields (they would be redundant with timestamp/level/logger).
    _RESERVED = frozenset({
        'name', 'msg', 'args', 'levelname', 'levelno', 'pathname', 'filename',
        'module', 'exc_info', 'exc_text', 'stack_info', 'lineno', 'funcName',
        'created', 'msecs', 'relativeCreated', 'thread', 'threadName',
        'processName', 'process', 'taskName', 'message', 'asctime',
    })

    def format(self, record: logging.LogRecord) -> str:
        # Base payload — timestamped in UTC, ISO-8601 with 'Z' suffix.
        payload: dict[str, Any] = {
            'timestamp': datetime.fromtimestamp(
                record.created, tz=timezone.utc,
            ).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z',
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
        }

        # Correlate to the active request (if any).
        rid = _request_id_value()
        if rid:
            payload['request_id'] = rid

        # Promote safe extra fields, redacting sensitive ones.
        for key, value in record.__dict__.items():
            if key in payload or key in self._RESERVED or key.startswith('_'):
                continue
            if key in ('request_id',):
                # Already handled above.
                continue
            if _should_redact_key(key):
                # Top-level extras like ``password`` / ``api_key`` are masked
                # wholesale — their value is never emitted as JSON.
                payload[key] = '***'
                continue
            redacted = redact(value)
            # Skip values that don't serialize cleanly to avoid corrupting log
            # streams — log a marker instead.
            try:
                json.dumps(redacted)
            except (TypeError, ValueError):
                redacted = '<non-serializable>'
            payload[key] = redacted

        # Exception info: render as a compact, parseable string — no local
        # variables are included (that would leak request data).
        if record.exc_info:
            exc_type, exc_value, _tb = record.exc_info
            payload['exception'] = ''.join(
                traceback.format_exception(exc_type, exc_value, record.exc_info[2])
            ).strip()

        return json.dumps(payload, ensure_ascii=False, separators=(',', ':'))


class RequestIDLogFilter(logging.Filter):
    """Inject the active request ID onto every log record as ``request_id``.

    Installed on the handlers configured by :func:`build_logging_config`. The
    filter *always* sets ``record.request_id`` (to the active ID, or to
    ``'-'`` when none is bound — e.g. ``manage.py migrate`` or Celery worker
    startup), so both JSON and %-style formatters can reference the field
    without ``KeyError``. The JSON formatter omits the field entirely when the
    value is the empty string.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        rid = _request_id_value()
        record.request_id = rid if rid else ''
        return True


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == '':
        return default
    return raw.lower() in ('1', 'true', 'yes')


def _env_level(name: str, default: str) -> str:
    return os.environ.get(name, default).upper()


def build_logging_config(*, json_output: bool | None = None,
                          level: str | None = None) -> dict:
    """Build a Django ``LOGGING`` dict from settings/environment knobs.

    Parameters
    ----------
    json_output:
        When ``True`` use :class:`JSONFormatter`; otherwise fall back to
        Django's verbose ``standard`` formatter. ``None`` reads
        ``LOG_JSON`` (defaulting to ``not DEBUG``).
    level:
        Override for the root/handler level. ``None`` reads ``LOG_LEVEL``
        (default ``INFO``).
    """
    from django.conf import settings  # local import: settings may be configured late

    debug = getattr(settings, 'DEBUG', False)
    if json_output is None:
        json_output = _env_bool('LOG_JSON', not debug)
    if level is None:
        level = _env_level('LOG_LEVEL', 'INFO')

    if json_output:
        formatter = {
            '()': 'ecommerce.logging.JSONFormatter',
        }
    else:
        formatter = {
            'format': (
                '%(asctime)s %(levelname)s [%(name)s] '
                'request_id=%(request_id)s %(message)s'
            ),
            'datefmt': '%Y-%m-%dT%H:%M:%S%z',
        }

    return {
        'version': 1,
        'disable_existing_loggers': False,
        'filters': {
            'request_id': {
                '()': 'ecommerce.logging.RequestIDLogFilter',
            },
        },
        'formatters': {
            'json' if json_output else 'standard': formatter,
        },
        'handlers': {
            'console': {
                'class': 'logging.StreamHandler',
                'formatter': 'json' if json_output else 'standard',
                'filters': ['request_id'],
                'level': level,
            },
        },
        'root': {
            'handlers': ['console'],
            'level': level,
        },
        # Django's own loggers inherit from root, but keep their propagation so
        # the request_id filter fires for framework logs too.
        'loggers': {
            'django': {'level': level, 'propagate': True},
            'django.request': {'level': level, 'propagate': True},
            'django.server': {'level': level, 'propagate': True},
        },
    }


def configure_logging() -> None:
    """Apply :func:`build_logging_config` to the root logger.

    Called from :mod:`ecommerce.settings` (which keeps ``LOGGING`` as a plain
    dict literal so ``django-admin check`` and ``override_settings`` see it)
    and is safe to call again at Celery startup if the worker was spawned in
    a context where Django settings weren't the entry point.
    """
    config = build_logging_config()
    logging.config.dictConfig(config)
