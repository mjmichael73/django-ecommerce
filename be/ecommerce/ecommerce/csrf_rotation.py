"""CSRF token rotation for identity state changes.

Card ef86d264 — Phase 1.1 (critical security): a CSRF token that was valid
before an identity transition (login, registration, logout) must not stay
valid after it, otherwise an attacker who fixed a token in the victim's
browser can replay it once the victim authenticates.

What Django 5.0.3 does on its own (verified in the pinned image):

* ``django.contrib.auth.login()`` calls ``rotate_token(request)``, so the
  built-in ``LoginView`` — and every view that calls ``login()`` — rotates
  the token already.
* ``django.contrib.auth.logout()`` does **not** rotate the token. Only
  ``django.contrib.auth.views.LogoutView`` calls it, so the token issued
  before a logout survives the logout.

The helpers below make the rotation explicit for the flows this app owns,
so the guarantee does not depend on framework internals.
"""
from django.conf import settings
from django.contrib.auth import login as auth_login
from django.middleware.csrf import rotate_token


def rotate_csrf_token(request):
    """Rotate the CSRF token bound to ``request``.

    The new secret is written to ``request.META`` and sent back to the
    client as the ``csrftoken`` cookie by ``CsrfViewMiddleware``.
    """
    rotate_token(request)


def login_with_csrf_rotation(request, user, backend=None):
    """Log ``user`` in and rotate the CSRF token.

    The token is rotated before the session is upgraded, so a token issued
    for the anonymous session can never be reused for the authenticated one.

    ``backend`` defaults to the backend that authenticated ``user`` (the
    ``user.backend`` attribute set by ``django.contrib.auth.authenticate``),
    falling back to the FIRST configured backend in
    ``settings.AUTHENTICATION_BACKENDS`` for directly-created users
    (registration, seeding) — under one backend that is Django's implicit
    ModelBackend, so behavior is unchanged in the disabled regime, and
    ``django.contrib.auth.login()`` always receives the explicit backend it
    demands once more than one backend is configured (social login, card
    hhde4h24).
    """
    rotate_csrf_token(request)
    if backend is None:
        backend = getattr(user, 'backend', None)
    if backend is None:
        backend = settings.AUTHENTICATION_BACKENDS[0]
    auth_login(request, user, backend=backend)
