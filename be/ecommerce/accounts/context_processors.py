"""Template context processor for social authentication (card hhde4h24).

Exposes the enablement flag so the login template can show the
"Continue with Google" button only when the provider is actually wired
(GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET both set in the environment).
Registered unconditionally in settings.TEMPLATES — reading the flag is safe
in both regimes, ``SOCIAL_AUTH_GOOGLE_ENABLED`` is a plain settings bool.
"""
from django.conf import settings


def social_auth(request):
    return {
        'social_google_enabled': settings.SOCIAL_AUTH_GOOGLE_ENABLED,
    }
