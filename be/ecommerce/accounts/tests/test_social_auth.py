"""Tests for social authentication (Google OAuth2 via django-allauth).

Card hhde4h24. The Google provider is fully wired only when BOTH
GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are set in the environment
(ecommerce.social_auth_config is the single source for that wiring);
otherwise the app loads without any allauth machinery at all.

All tests are network-free: allauth builds the Google authorization URL
locally from SOCIALACCOUNT_PROVIDERS, so a GET on /accounts/google/login/
must 302 to https://accounts.google.com/o/oauth2/ without contacting
anything, and the callback without a `code` renders allauth's local
authentication_error template.

The enabled regime cannot rely on a plain `override_settings(
INSTALLED_APPS=...)` alone: Django's test signals only clear caches for an
INSTALLED_APPS override; and override_settings.enable() rebuilds the app
registry BEFORE the other options land (allauth's AccountConfig.ready()
would then not see the new MIDDLEWARE). So the enabled-regime test class
rebuilds the registry explicitly (apps.set_installed_apps()), applies
allauth's migrations to the test database (rolled back at class end), and
mirrors MIDDLEWARE and the URLconf exactly as settings.py wires them. When
the process itself already booted in the enabled regime (GOOGLE_* present
at settings import), the tests simply run against that native state.
"""
import os
import sys
import types
from importlib import import_module
from unittest import skipUnless

from django.apps import apps
from django.conf import settings
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.test.signals import reset_template_engines, update_installed_apps
from django.urls import NoReverseMatch, include, path, reverse

from ecommerce.social_auth_config import (
    ALLAUTH_APPS,
    ALLAUTH_MIDDLEWARE,
    google_social_auth,
)

_DUMMY_CLIENT_ID = 'test-google-client-id.apps.googleusercontent.com'
_DUMMY_CLIENT_SECRET = 'test-google-client-secret'

_ENABLED_ENV = {
    'GOOGLE_CLIENT_ID': _DUMMY_CLIENT_ID,
    'GOOGLE_CLIENT_SECRET': _DUMMY_CLIENT_SECRET,
}


def _google_disabled_in_env():
    return not os.environ.get('GOOGLE_CLIENT_ID') and not os.environ.get(
        'GOOGLE_CLIENT_SECRET'
    )


class SocialAuthHelperTests(SimpleTestCase):
    """Pure-unit tests for the enablement decision (env -> wiring)."""

    def test_unset_env_disables(self):
        self.assertIsNone(google_social_auth({}))

    def test_whitespace_only_credentials_disable(self):
        self.assertIsNone(
            google_social_auth(
                {'GOOGLE_CLIENT_ID': '  ', 'GOOGLE_CLIENT_SECRET': ' '}
            )
        )

    def test_only_secret_disables(self):
        self.assertIsNone(
            google_social_auth({'GOOGLE_CLIENT_SECRET': 'secret-value'})
        )

    def test_both_credentials_enable(self):
        cfg = google_social_auth(_ENABLED_ENV)
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg.providers['google']['APP']['client_id'], _DUMMY_CLIENT_ID)
        self.assertEqual(cfg.providers['google']['APP']['secret'], _DUMMY_CLIENT_SECRET)
        self.assertIn(
            'allauth.account.auth_backends.AuthenticationBackend', cfg.auth_backends
        )


@skipUnless(
    _google_disabled_in_env(),
    'Google credentials are set in this process; the disabled regime is not active',
)
class SocialAuthDisabledTests(TestCase):
    """Default regime: GOOGLE_* unset — the app behaves exactly as today."""

    def test_flag_off(self):
        self.assertIs(settings.SOCIAL_AUTH_GOOGLE_ENABLED, False)

    def test_allauth_apps_not_installed(self):
        installed = {c.name for c in apps.get_app_configs()}
        for app_name in ALLAUTH_APPS:
            self.assertNotIn(app_name, installed)
            self.assertNotIn(app_name, settings.INSTALLED_APPS)

    def test_allauth_middleware_not_in_middleware(self):
        self.assertNotIn(ALLAUTH_MIDDLEWARE, settings.MIDDLEWARE)

    def test_login_page_without_google_button(self):
        resp = self.client.get('/accounts/login/')
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Continue with Google')

    def test_google_urls_do_not_resolve(self):
        with self.assertRaises(NoReverseMatch):
            reverse('google_login')

    def test_google_login_page_is_404(self):
        resp = self.client.get('/accounts/google/login/')
        self.assertEqual(resp.status_code, 404)

    def test_google_callback_page_is_404(self):
        resp = self.client.get('/accounts/google/login/callback/')
        self.assertEqual(resp.status_code, 404)


class SocialAuthEnabledTests(TestCase):
    """Credentials set: allauth fully wired, no network touched.

    The app registry is rebuilt via apps.set_installed_apps() — this class
    cannot rely on override_settings for INSTALLED_APPS because its
    enable() rebuilds the registry before the other options land
    (AccountConfig.ready() would not yet see the new MIDDLEWARE). allauth's
    migrations are applied to the test database inside the class atomics so
    everything is rolled back at class end.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()  # TestCase: enters the class atomics
        if 'allauth.account' in settings.INSTALLED_APPS:
            # The process booted in the enabled regime (GOOGLE_* set at
            # settings import): INSTALLED_APPS/MIDDLEWARE/the urlconf are
            # already fully wired and the test DB was migrated with
            # allauth's models — run the tests against that real state.
            cls._wired_here = False
            return
        cls._wired_here = True
        cfg = google_social_auth(_ENABLED_ENV)
        enabled_apps = list(settings.INSTALLED_APPS) + list(ALLAUTH_APPS)
        cls._urlconf_override = None
        # Middleware + flags FIRST: allauth's AccountConfig.ready() runs
        # during the registry rebuild below and requires AccountMiddleware.
        cls._override = override_settings(
            MIDDLEWARE=list(settings.MIDDLEWARE) + [ALLAUTH_MIDDLEWARE],
            SOCIALACCOUNT_PROVIDERS=cfg.providers,
            # Same value settings.py sets in the enabled regime (allauth 65
            # defaults GET /accounts/google/login/ to an interstitial page;
            # the login page's link navigates via GET).
            SOCIALACCOUNT_LOGIN_ON_GET=True,
            AUTHENTICATION_BACKENDS=cfg.auth_backends,
            ACCOUNT_EMAIL_VERIFICATION='none',
            SOCIAL_AUTH_GOOGLE_ENABLED=True,
        )
        cls._override.enable()
        try:
            # override_settings cannot carry INSTALLED_APPS here: its
            # enable() rebuilds the registry BEFORE applying the other
            # options, so AccountConfig.ready() would then read the OLD
            # MIDDLEWARE. Rebuild the registry explicitly instead.
            apps.set_installed_apps(enabled_apps)
            # The manual rebuild skips the cache clearing that
            # django.test.signals performs for a settings INSTALLED_APPS
            # override — repeat it here, or stale caches (app-template dirs
            # and the cached template engine's template_libraries — allauth
            # ships `{% load allauth %}` and its templates under the
            # `allauth` package root) make allauth's own templates fail.
            update_installed_apps(setting='INSTALLED_APPS')
            reset_template_engines(setting='INSTALLED_APPS')
            # allauth ships its own migrations; the test DB was created with
            # the disabled app set, so apply them now (rolled back per class).
            call_command('migrate', run_syncdb=False, interactive=False, verbosity=0)
            cls._urlconf_override = override_settings(
                ROOT_URLCONF=cls._build_enabled_urlconf()
            )
            cls._urlconf_override.enable()
        except Exception:
            cls._teardown_enabled()
            raise

    @classmethod
    def _build_enabled_urlconf(cls):
        """Synthetic urlconf: the base project urlpatterns plus allauth's
        include appended after the local accounts/ include — exactly what
        ecommerce/urls.py produces when the credentials are set. Built here,
        AFTER the registry rebuild, because `include('allauth.urls')` imports
        allauth eagerly; in the disabled regime that import fails by design
        (allauth must not load), so the urlconf must not exist at module
        import time.
        """
        name = f'{__name__}.{cls.__name__}_urls'
        mod = types.ModuleType(name)
        base = import_module('ecommerce.urls')
        mod.urlpatterns = list(base.urlpatterns) + [
            path('accounts/', include('allauth.urls')),
        ]
        sys.modules[name] = mod
        return name

    @classmethod
    def _teardown_enabled(cls):
        # Must be balanced even on failure (apps registry contract).
        apps.unset_installed_apps()
        cls._override.disable()
        if cls._urlconf_override is not None:
            cls._urlconf_override.disable()
            cls._urlconf_override = None

    @classmethod
    def tearDownClass(cls):
        try:
            if cls._wired_here:
                cls._teardown_enabled()
        finally:
            super().tearDownClass()

    def test_flag_on(self):
        self.assertIs(settings.SOCIAL_AUTH_GOOGLE_ENABLED, True)

    def test_allauth_apps_installed(self):
        # Note: the registry is rebuilt explicitly (see setUpClass); the
        # settings.INSTALLED_APPS list itself is only fully set in production
        # (guarded there by settings.py, verified by `manage.py check` with
        # the credentials set). The registry drives all runtime lookups.
        installed = {c.name for c in apps.get_app_configs()}
        for app_name in ALLAUTH_APPS:
            self.assertIn(app_name, installed)
        # Provider config app (registry label of the provider app package).
        apps.get_app_config('google')

    def test_allauth_middleware_present(self):
        self.assertIn(ALLAUTH_MIDDLEWARE, settings.MIDDLEWARE)

    def test_google_urls_resolve(self):
        self.assertEqual(reverse('google_login'), '/accounts/google/login/')
        self.assertEqual(reverse('google_callback'), '/accounts/google/login/callback/')

    def test_login_page_shows_google_button(self):
        resp = self.client.get('/accounts/login/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Continue with Google')
        # CSP (card f3d54f76): the button links to OUR backend; Google is
        # reached only through the server-side redirect afterward.
        self.assertContains(resp, 'href="/accounts/google/login/"')
        self.assertNotContains(resp, 'accounts.google.com')

    def test_context_processor_flag_on(self):
        resp = self.client.get('/accounts/login/')
        self.assertIs(resp.context['social_google_enabled'], True)

    def test_google_login_redirects_to_google_without_network(self):
        resp = self.client.get('/accounts/google/login/')
        self.assertEqual(resp.status_code, 302)
        location = resp['Location']
        self.assertTrue(
            location.startswith('https://accounts.google.com/o/oauth2/'),
            f'unexpected redirect target: {location}',
        )

    def test_google_callback_without_code_renders_error(self):
        # No `code` and no `state`: allauth must render its local error page
        # without reaching the provider.
        resp = self.client.get('/accounts/google/login/callback/')
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'socialaccount/authentication_error.html')
