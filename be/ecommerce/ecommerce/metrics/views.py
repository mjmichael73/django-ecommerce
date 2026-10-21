"""Views for Prometheus metrics and health checks.

Card g37aggbd — Phase 4.1 monitoring.

* ``/metrics`` — returns the Prometheus text exposition. Only mounted when
  ``PROMETHEUS_ENABLED`` is set (see ``ecommerce/urls.py``). Access is
  optionally restricted by ``PROMETHEUS_METRICS_ALLOWED_IPS``.
* ``/health/`` — liveness/readiness probe for the load balancer / nginx.
  Always available, returns ``200`` with a JSON status when the DB can be
  queried, ``503`` otherwise. Does not require metrics to be enabled.
"""
from __future__ import annotations

import json
from datetime import datetime

from django.conf import settings
from django.db import connection, OperationalError
from django.http import HttpResponse, JsonResponse
from ecommerce.metrics.registry import (
    METRICS_CONTENT_TYPE,
    is_metrics_enabled,
    metrics_exposition,
)


def _client_ip(request) -> str:
    """Return the request's source IP for allowlist checks.

    We only look at REMOTE_ADDR — never X-Forwarded-For without
    ``DJANGO_BEHIND_PROXY`` — matching the rate-limit middleware's policy.
    """
    return request.META.get('REMOTE_ADDR', '')


def metrics_view(request):
    """Scrape endpoint. Returns 404 when metrics are disabled.

    The URL is only mounted when ``PROMETHEUS_ENABLED`` is set, but the view
    also guards at runtime so it is safe if the setting changes between
    deployment and a stale URL registration.
    """
    if not is_metrics_enabled():
        from django.http import Http404

        raise Http404('/metrics is not enabled. Set PROMETHEUS_ENABLED=1.')

    allowed_ips = getattr(settings, 'PROMETHEUS_METRICS_ALLOWED_IPS', set())
    if allowed_ips:
        client = _client_ip(request)
        if client not in allowed_ips:
            return HttpResponse(status=403)

    body = metrics_exposition()
    return HttpResponse(body, content_type=METRICS_CONTENT_TYPE)


def health_view(request):
    """Liveness/readiness probe.

    Returns ``200`` with a JSON body when the database connection works;
    ``503`` when it does not. This lets the load balancer / nginx pull a
    health signal without exposing metrics.
    """
    healthy = True
    detail = {}

    # DB probe — a trivial query. Wrapped so a connection error yields a
    # 503 rather than a 500 to the probe caller.
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
    except OperationalError:
        healthy = False
        detail['database'] = 'unavailable'
    else:
        detail['database'] = 'ok'

    now = datetime.utcnow().isoformat() + 'Z'
    payload = {
        'status': 'ok' if healthy else 'unavailable',
        'timestamp': now,
        **detail,
    }
    status = 200 if healthy else 503
    return JsonResponse(payload, status=status)
