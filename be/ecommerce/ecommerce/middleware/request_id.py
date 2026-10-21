"""Request-ID correlation middleware.

Card 6cc44f8g — Phase 4.1 monitoring: structured logging with request IDs.

This middleware assigns a correlation ID to every inbound HTTP request and
makes it available to all log calls issued during that request, so every log
line carrying ``request_id`` can be traced back to a single client request
across the Gunicorn workers, Celery tasks and Stripe/webhook boundary.

Design
------
* **Generation:** a fresh ``uuid4`` hex when the client did not supply a
  ``X-Request-Id`` header. Supplied values are validated (length + charset)
  before reuse — an invalid header is treated as absent so a malformed or
  hostile caller cannot inject arbitrary IDs.
* **Propagation:** the ID is held in a :class:`contextvars.ContextVar` so it
  survives across ``await`` points (async views) and is inherited by Celery
  tasks that pick up the value before the worker's task wrapper clears the
  request context.
* **Response header:** the winning ID (generated or accepted) is echoed back
  on ``X-Request-Id`` so clients can correlate their own tracing.
* **Structured logging:** a :class:`~logging.Filter` (``RequestIDLogFilter``)
  injects ``request_id`` into every record; together with the JSON formatter
  in :mod:`ecommerce.logging` this produces machine-readable log lines.
* **Sensitive data:** the filter never logs request *bodies* or *cookies* —
  it only carries the already-generated ID. Body/cookie redaction is handled
  by the JSON formatter's secret-scrubbing pass (see
  :func:`ecommerce.logging.redact`).

The middleware is registered *outermost* (position 0) in ``MIDDLEWARE`` so the
ID is established before every other layer runs and the response header is
present even on early rejections (429, 400, SecurityMiddleware 301s).
"""
from __future__ import annotations

import re
from contextvars import ContextVar

from ecommerce.logging import REQUEST_ID_HEADER, request_id as current_request_id

# A request ID is an opaque correlation token: 16–128 hex/base62-ish chars are
# plenty. We are deliberately permissive about *format* (callers may supply any
# ASCII token) but bound the length to keep it a cheap index key.
_REQUEST_ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,128}$')

# Populated by the outermost middleware; read by the request_id() accessor and
# the Celery task wrapper.
_request_id_var: ContextVar[str] = current_request_id

__all__ = [
    'RequestIDMiddleware',
    'request_id',
    'propagate_request_id',
]


def _valid_existing(value: str) -> bool:
    """Return True when *value* is an acceptable caller-supplied request ID."""
    return bool(value) and bool(_REQUEST_ID_RE.match(value))


def _generate() -> str:
    """Generate a fresh, lowercase, URL-safe request ID."""
    import uuid
    return uuid.uuid4().hex


class RequestIDMiddleware:
    """Generate/accept a request ID and bind it to the active context.

    Installed outermost in ``MIDDLEWARE`` so the ID exists for every log call
    made downstream, and so the response header is present on early exits.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Accept a caller-supplied ID (for distributed tracing ingress) or
        # mint a fresh one. The accepted/generated value is stored both on the
        # request (for view-level access) and in the context var (for log
        # filters running in middleware/views/tasks).
        supplied = request.headers.get(REQUEST_ID_HEADER, '')
        rid = supplied if _valid_existing(supplied) else _generate()

        # Bind into the request context first (so downstream middleware/views
        # see it) and keep a token to restore on exit.
        token = _request_id_var.set(rid)
        request.request_id = rid  # convenience attribute for views

        try:
            response = self.get_response(request)
        finally:
            # Always restore the context, even when the view raised. A
            # concurrent/early-exit exception must not leak this request's ID
            # to the next request (or to a sibling async task).
            _request_id_var.reset(token)

        # Echo the correlation ID on the response. Only reached when the view
        # returned normally — an exception propagates without the header
        # (Django's error handlers will produce their own response if needed).
        response[REQUEST_ID_HEADER] = rid
        return response


def request_id() -> str:
    """Return the active request ID, or an empty string when none is bound.

    Used by :class:`~ecommerce.logging.RequestIDLogFilter` and by Celery
    task wrappers that want to attach the originating request ID to a task
    log record.
    """
    return _request_id_var.get('')


def propagate_request_id() -> dict:
    """Return kwargs to forward the active request ID to a Celery task.

    Dispatch sites call ``task.delay(..., **propagate_request_id())`` so the
    worker can rebind the contextvar and keep log lines correlated. Returns
    an empty dict when no request ID is active (e.g. periodic tasks), so
    callers can splat it unconditionally.

    The value is passed both as a task kwarg (``_request_id``) and as a
    Celery message header (``request_id``) so it survives broker round-trips
    and is readable by the worker's task-execution hook.
    """
    rid = _request_id_var.get('')
    if not rid:
        return {}
    return {'_request_id': rid}
