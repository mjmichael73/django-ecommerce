"""Enablement wiring for Google social authentication (card hhde4h24).

Pure-python helper — same pattern as ``db_config``/``cache_config``: computes
from environment variables only, no Django imports, so settings and tests can
share the exact same wiring decision.

Rule (card g89b45f5 discipline, no credentials in the repo): the Google
provider is fully wired only when BOTH ``GOOGLE_CLIENT_ID`` and
``GOOGLE_CLIENT_SECRET`` are set and non-empty. Otherwise the app loads
without any allauth machinery at all — INSTALLED_APPS, MIDDLEWARE and the
urlconf are all built conditionally in settings/urls, because they are
computed at settings-import time (there is no runtime toggle).
"""
from dataclasses import dataclass

# Django app label -> config value; appended to settings.INSTALLED_APPS when enabled.
ALLAUTH_APPS = (
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'allauth.socialaccount.providers.google',
)

# django-allauth >= 65 requires this middleware (enforced by a startup
# message check); older versions do not know it.
ALLAUTH_MIDDLEWARE = 'allauth.account.middleware.AccountMiddleware'


@dataclass(frozen=True)
class GoogleSocialAuthConfig:
    """Everything settings.py needs to wire the Google provider."""

    client_id: str
    client_secret: str

    @property
    def providers(self) -> dict:
        """Settings-only provider app (SOCIALACCOUNT_PROVIDERS, no SocialApp row).

        allauth builds the authorize/callback URLs entirely from this
        configuration — no consult of the SocialApp database table, so no
        admin setup or extra migrations are needed.
        """
        return {
            'google': {
                'APP': {
                    'client_id': self.client_id,
                    'secret': self.client_secret,
                    'key': '',
                },
                'SCOPE': ['profile', 'email'],
                'AUTH_PARAMS': {'access_type': 'online'},
            },
        }

    @property
    def auth_backends(self) -> list:
        """allauth's backend must sit next to Django's ModelBackend for the
        social login to complete; without it only local logins would work."""
        return [
            'django.contrib.auth.backends.ModelBackend',
            'allauth.account.auth_backends.AuthenticationBackend',
        ]


def google_social_auth(env) -> GoogleSocialAuthConfig | None:
    """Return the Google provider config, or None when social auth is off.

    ``env`` maps variable names to strings (normally ``os.environ``).
    Whitespace-only values count as unset.
    """
    client_id = env.get('GOOGLE_CLIENT_ID', '').strip()
    client_secret = env.get('GOOGLE_CLIENT_SECRET', '').strip()
    if not (client_id and client_secret):
        return None
    return GoogleSocialAuthConfig(client_id=client_id, client_secret=client_secret)
