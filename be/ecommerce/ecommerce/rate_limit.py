"""Per-client rate limiting for state-changing views.

Card 25ahc7hg — Phase 1.1 (critical security): checkout and coupon
application are POST-only attack surfaces (order spam, coupon guessing).
Every client IP gets a fixed-window counter per scope, kept in the Django
cache.

The window is the counter key's own cache timeout, so there is no
bookkeeping outside the cache:

    cache.add(key, 0, timeout=period)   # first attempt starts the window
    count = cache.incr(key)             # atomic increment, TTL unchanged

``add()`` is atomic — only the first of several racing requests creates
the key — and ``incr()`` never extends the TTL, so the window is a true
fixed window and concurrent attempts cannot reset it.

Django's cache API cannot read a key's remaining TTL, so the same window
deadline is stored next to the counter (``...:window``, same timeout) and
read only when a request is rejected, to fill ``Retry-After``. Both keys
expire with the window; nothing else is kept.

Limits are settings tuples ``(attempts, period_seconds)`` and are read on
every request so tests (and operators) can override them:

    RATE_LIMIT_CHECKOUT = (10, 3600)   # orders order_create
    RATE_LIMIT_COUPON = (10, 3600)     # coupons coupon_apply
"""
from __future__ import annotations

import ipaddress
import math
import time
from functools import wraps

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse

# Fallback values, mirrored by settings.py — kept here so the module is
# usable (and testable) before settings define them.
CODE_DEFAULTS = {
    'checkout': (10, 3600),
    'coupon': (10, 3600),
    'email_verify': (5, 3600),
    'password_reset': (5, 3600),
}

TOO_MANY_REQUESTS_BODY = 'Too many requests. Please try again later.'


def _limit(scope: str) -> tuple[int, int]:
    """Return ``(attempts, period_seconds)`` for *scope* from settings."""
    try:
        default = CODE_DEFAULTS[scope]
    except KeyError:
        raise ImproperlyConfigured(
            f'Unknown rate-limit scope {scope!r}; known scopes: '
            f'{", ".join(sorted(CODE_DEFAULTS))}.'
        ) from None
    limit, period = getattr(settings, f'RATE_LIMIT_{scope.upper()}', default)
    return int(limit), int(period)


def _public_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Return *value* as an IP address when it is globally routable."""
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return None
    return address if address.is_global else None


def client_ip(request) -> str:
    """Return the address that identifies *request*'s rate-limit bucket.

    ``REMOTE_ADDR`` is the source of truth. ``X-Forwarded-For`` is only
    consulted when the deployment declares that Django sits behind a proxy
    that writes it (``DJANGO_BEHIND_PROXY`` → ``RATE_LIMIT_TRUST_X_FORWARDED_FOR``,
    the same flag that enables ``SECURE_PROXY_SSL_HEADER``). Otherwise a
    direct client could pick its own bucket by sending the header.

    When trusted, the first *globally routable* hop wins. Private/loopback
    hops (Docker networks, internal proxies) are not usable as bucket names:
    they would either be forgeable or collapse every client onto one bucket.
    """
    remote_addr = request.META.get('REMOTE_ADDR') or 'unknown'
    if not getattr(settings, 'RATE_LIMIT_TRUST_X_FORWARDED_FOR', False):
        return remote_addr
    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR', '')
    for hop in forwarded_for.split(','):
        address = _public_ip(hop.strip())
        if address is not None:
            return str(address)
    return remote_addr


def _retry_after(deadline_key: str, period: int, now: float) -> int:
    """Seconds left in the current window, for the ``Retry-After`` header."""
    deadline = cache.get(deadline_key)
    if deadline is None:
        return period
    return max(1, min(period, math.ceil(deadline - now)))


def rate_limit(scope: str, methods: tuple[str, ...] = ('POST',)):
    """Limit *methods* requests to *scope*'s window, per client IP.

    Other methods (GET, OPTIONS, HEAD, ...) and methods a view rejects with
    405 pass straight through without consuming quota — apply this decorator
    *outside* ``require_POST`` so a rejected request never counts.
    """

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if request.method not in methods:
                return view(request, *args, **kwargs)

            limit, period = _limit(scope)
            if period <= 0:
                # No window to count in: rate limiting is disabled.
                return view(request, *args, **kwargs)

            now = time.time()
            key = f'rate:{scope}:{client_ip(request)}'
            deadline_key = f'{key}:window'
            # The first request of a window starts the deadline; later ones
            # leave it alone, exactly like the counter.
            cache.add(deadline_key, now + period, timeout=period)
            cache.add(key, 0, timeout=period)
            try:
                count = cache.incr(key)
            except ValueError:
                # The window expired between add() and incr(): start over.
                cache.add(key, 0, timeout=period)
                count = cache.incr(key)

            if count > limit:
                response = HttpResponse(
                    TOO_MANY_REQUESTS_BODY,
                    status=429,
                    content_type='text/plain',
                )
                response['Retry-After'] = str(_retry_after(deadline_key, period, now))
                return response
            return view(request, *args, **kwargs)

        return wrapper

    return decorator
