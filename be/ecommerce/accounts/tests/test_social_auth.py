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

The enabled regime cannot rely on a plain, standalone `override_settings(
INSTALLED_APPS=...)`: its enable() rebuilds the app registry BEFORE the
other options land, so allauth's AccountConfig.ready() (which requires
AccountMiddleware in settings.MIDDLEWARE) would fire too early. When the
process boots in the disabled regime, the enabled-regime test class
therefore applies a two-step override: outer MIDDLEWARE+flags first, then
inner INSTALLED_APPS (whose enable() performs the registry rebuild), then
allauth's migrations on the test database — all rolled back at class end —
and mirrors the URLconf with exactly what ecommerce/urls.py appends. The
result is asserted against the REAL settings (INSTALLED_APPS, MIDDLEWARE,
ACCOUNT_EMAIL_VERIFICATION). When the process itself already booted in the
enabled regime (GOOGLE_* present at settings import), the tests simply run
against that native state.
"""
import os
import sys
import types
from importlib import import_module
from unittest import skipUnless

from django.apps import apps
from django.conf import settings
from django.contrib import admin
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.test.signals import reset_template_engines
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

    When the process booted in the disabled regime, the production wiring is
    mirrored with NESTED override_settings so every assertion below reads
    the real settings state: outer MIDDLEWARE+flags (applied first — the
    inner override's enable() rebuilds the app registry BEFORE the settings
    holder swap, and AccountConfig.ready() must already see AccountMiddleware),
    then inner INSTALLED_APPS, then allauth's own migrations on the test
    database — all rolled back at class end. When the process itself already
    booted in the enabled regime (GOOGLE_* present at settings import), no
    machinery runs and the native state is asserted directly.
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
        cls._outer_override = None
        cls._inner_override = None
        cls._urlconf_override = None
        cls._urlconf_name = None
        try:
            outer = override_settings(
                MIDDLEWARE=list(settings.MIDDLEWARE) + [ALLAUTH_MIDDLEWARE],
                SOCIALACCOUNT_PROVIDERS=cfg.providers,
                # Same value settings.py sets in the enabled regime (allauth
                # 65 defaults GET /accounts/google/login/ to an interstitial
                # page; the login page's link navigates via GET).
                SOCIALACCOUNT_LOGIN_ON_GET=True,
                AUTHENTICATION_BACKENDS=cfg.auth_backends,
                ACCOUNT_EMAIL_VERIFICATION='none',
                SOCIAL_AUTH_GOOGLE_ENABLED=True,
            )
            outer.enable()
            cls._outer_override = outer
            # The inner override's enable() rebuilds the app registry
            # (apps.set_installed_apps happens BEFORE the settings holder
            # swap); the outer override is live by then, so
            # AccountConfig.ready() sees the required AccountMiddleware, and
            # the INSTALLED_APPS receivers clear the app/template caches.
            inner = override_settings(INSTALLED_APPS=enabled_apps)
            inner.enable()
            cls._inner_override = inner
            # allauth ships its own migrations; the test DB was created with
            # the disabled app set, so apply them now (rolled back per class).
            call_command('migrate', run_syncdb=False, interactive=False, verbosity=0)
            urlconf_override = override_settings(
                ROOT_URLCONF=cls._build_enabled_urlconf()
            )
            urlconf_override.enable()
            cls._urlconf_override = urlconf_override
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
        cls._urlconf_name = name
        return name

    @classmethod
    def _teardown_enabled(cls):
        """Balanced teardown for partial failures: urlconf first, then the
        app registry (inner), then the settings holder (outer), each only
        when actually applied. The template-engine caches (an allauth-aware
        engine and its template_libraries) are rebuilt last so no later test
        module inherits an enabled-regime engine."""
        if cls._urlconf_override is not None:
            cls._urlconf_override.disable()
            cls._urlconf_override = None
        if cls._urlconf_name:
            sys.modules.pop(cls._urlconf_name, None)
            cls._urlconf_name = None
        if cls._inner_override is not None:
            # disable() also restores the original app registry.
            cls._inner_override.disable()
            cls._inner_override = None
        if cls._outer_override is not None:
            cls._outer_override.disable()
            cls._outer_override = None
        # Card ga4e3hg5: allauth's ModelAdmins stay in admin.site._registry
        # after the app registry is restored, so the next admin page built
        # with the disabled app set raises LookupError on the vanished
        # 'account'/'socialaccount' app configs. Drop registrations whose app
        # is no longer installed so later test modules see a clean admin.
        installed_labels = {config.label for config in apps.get_app_configs()}
        for model in list(admin.site._registry):
            if model._meta.app_label not in installed_labels:
                admin.site.unregister(model)
        reset_template_engines(setting='INSTALLED_APPS')

    @classmethod
    def tearDownClass(cls):
        try:
            if cls._wired_here:
                cls._teardown_enabled()
        finally:
            super().tearDownClass()

    def test_flag_on(self):
        self.assertIs(settings.SOCIAL_AUTH_GOOGLE_ENABLED, True)

    def test_enabled_wiring_is_in_settings(self):
        """The production wiring is actually IN the settings, not a test-
        built mirror: INSTALLED_APPS, MIDDLEWARE and the email-verification
        regime are asserted against django.conf.settings."""
        for app_name in ALLAUTH_APPS:
            self.assertIn(app_name, settings.INSTALLED_APPS)
        apps.get_app_config('google')
        self.assertIn(ALLAUTH_MIDDLEWARE, settings.MIDDLEWARE)
        self.assertEqual(settings.ACCOUNT_EMAIL_VERIFICATION, 'none')

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

    def test_local_registration_and_login_work_when_enabled(self):
        """Local auth flows are untouched in the enabled regime (card's
        binding rule): registering still logs the local user in, with the
        FIRST configured backend — django.contrib.auth.login() refuses to
        guess between backends, and login_with_csrf_rotation resolves it.
        """
        resp = self.client.post(
            '/accounts/register/',
            {
                'username': 'social-enabled-shopper',
                'email': 'social-enabled-shopper@example.com',
                'password1': 'complex-pass-phrase-99',
                'password2': 'complex-pass-phrase-99',
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], '/accounts/profile/')
        self.assertEqual(
            self.client.session['_auth_user_backend'],
            settings.AUTHENTICATION_BACKENDS[0],
        )

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
