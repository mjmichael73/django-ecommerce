"""Celery application bootstrap.

Card 6cc44f8g — Phase 4.1 monitoring: structured logging with request IDs.

The Celery app is configured from Django settings (namespace ``CELERY``) and
discovers tasks from every INSTALLED_APPS. Two logging hooks keep worker
logs consistent with the web layer:

* ``@app.on_after_finalize`` installs the project's ``LOGGING`` dict (the
  same one Django uses) so task log lines are JSON-structured and carry
  ``request_id`` when one is bound in the active context. This runs once
  per worker process at startup.
* Worker-side request-ID propagation: when a task is dispatched from a web
  request, the calling view stamps the originating request's ID onto the
  task's keyword arguments (``_request_id=``) or Celery message headers.
  The :func:`ecommerce.middleware.request_id.propagate_request_id` helper
  reads this contextvar; task code that wants to log with correlation reads
  ``request_id()`` (same accessor the middleware and log filter use).

Tasks that are *not* dispatched from a request (e.g. ``celery beat`` periodic
jobs) log without a ``request_id`` field — the JSON formatter omits the
field when none is bound, so those lines stay compact.
"""
import logging
import logging.config
import os

from celery import Celery

# Django settings must be configured before Celery reads CELERY_* settings.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ecommerce.settings')

app = Celery('ecommerce')

# Configure from settings before autodiscover so CELERY_* is honoured.
app.config_from_object('django.conf:settings', namespace='CELERY')


def _apply_django_logging():
    """Install Django's ``LOGGING`` config on the worker (JSON + request_id).

    Called once when the worker finishes finalizing. Falls back to a minimal
    JSON setup if Django settings can't be read (e.g. bare ``celery -A``
    without ``DJANGO_SETTINGS_MODULE``).
    """
    try:
        from django.conf import settings
        logging.config.dictConfig(settings.LOGGING)
    except Exception:
        _fallback_logging()


def _fallback_logging():
    """Minimal structured logging when Django settings aren't available."""
    logging.config.dictConfig({
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'json': {'()': 'ecommerce.logging.JSONFormatter'},
        },
        'filters': {
            'request_id': {'()': 'ecommerce.logging.RequestIDLogFilter'},
        },
        'handlers': {
            'celery': {
                'class': 'logging.StreamHandler',
                'formatter': 'json',
                'filters': ['request_id'],
                'level': 'INFO',
            },
        },
        'root': {'handlers': ['celery'], 'level': 'INFO'},
    })


@app.on_after_finalize.connect
def _worker_logging(sender, **kwargs):  # pragma: no cover -- smoke-tested separately
    """Install the Django LOGGING config on the worker once it's ready."""
    _apply_django_logging()


app.autodiscover_tasks()


# --- Task execution tracing ----------------------------------------------------
# When a task is dispatched from a web request with ``propagate_request_id()``
# (i.e. ``task.delay(..., _request_id=<rid>)``), Celery surfaces that kwarg in
# ``self.request`` and as a message header. We rebind the contextvar so the
# worker's log filter picks up the correlation ID and task log lines trace
# back to the originating HTTP request.
#
# We also record Prometheus metrics (card g37aggbd) for each task: count by
# name + success/failure and a duration histogram. The metric recording is
# best-effort — if ``prometheus_client`` is unavailable or metrics are
# disabled, the no-op meter absorbs the calls.
import time

from celery import Task as _CeleryTask

from ecommerce.sentry import init_sentry

# Initialise Sentry on the worker (card 5ggb4929). This runs at import time of
# celery.py (which is imported by ecommerce/__init__.py for every worker
# process). No-op when SENTRY_DSN is unset or sentry-sdk is not installed.
init_sentry()

_task_call = _CeleryTask.__call__


def _tracing_call(self, args, kwargs):
    """Rebind the request ID from the task context and record task metrics."""
    # Try the explicit kwarg first, then the message header.
    headers = getattr(self.request, 'headers', {}) or {}
    rid = kwargs.pop('_request_id', None) or headers.get('request_id') or ''
    if rid:
        try:
            from ecommerce.logging import request_id as _var
            _var.set(rid)
        except Exception:
            pass

    # Best-effort Prometheus instrumentation (card g37aggbd).
    from ecommerce.metrics.business import record_celery_task

    task_name = getattr(self, 'name', type(self).__name__)
    start = time.monotonic()
    try:
        result = _task_call(self, args, kwargs)
    except Exception:
        duration = time.monotonic() - start
        record_celery_task(task_name, 'failure', duration)
        raise
    else:
        duration = time.monotonic() - start
        record_celery_task(task_name, 'success', duration)
        return result


_CeleryTask.__call__ = _tracing_call
