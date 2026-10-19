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
from django.contrib.auth import login as auth_login
from django.middleware.csrf import rotate_token


def rotate_csrf_token(request):
    """Rotate the CSRF token bound to ``request``.

    The new secret is written to ``request.META`` and sent back to the
    client as the ``csrftoken`` cookie by ``CsrfViewMiddleware``.
    """
    rotate_token(request)


def login_with_csrf_rotation(request, user):
    """Log ``user`` in and rotate the CSRF token.

    The token is rotated before the session is upgraded, so a token issued
    for the anonymous session can never be reused for the authenticated one.
    """
    rotate_csrf_token(request)
    auth_login(request, user)
