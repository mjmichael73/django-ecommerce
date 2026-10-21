"""Tests for Sentry SDK initialisation (card 5ggb4929).

Covers:
* ``init_sentry`` is a no-op (returns False) when ``SENTRY_DSN`` is unset.
* ``init_sentry`` is idempotent: a second call is a no-op even with DSN set.
* ``init_sentry`` returns False (and logs a warning) when DSN is set but
  ``sentry-sdk`` is not installed — startup is never broken.
* ``init_sentry`` calls ``sentry_sdk.init`` exactly once with wired options
  when the DSN is set and the SDK is present. We inject a *fake*
  ``sentry_sdk`` module into ``sys.modules`` so the tests do not depend on
  whether the real package is installed — no real network calls.
* The Django/Celery integrations are passed through; toggling them off drops
  the corresponding integration.

The real ``sentry_sdk`` is never imported by these tests: a stub module with
real ``DjangoIntegration``/``CeleryIntegration`` classes and an ``init``
recorder replaces it in ``sys.modules`` for the duration of each test.
"""
import os
import sys
import types
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from ecommerce.sentry import init_sentry, reset_sentry_for_tests


def _clear_sentry_env():
    env = os.environ
    for key in list(env):
        if key.startswith('SENTRY_'):
            env.pop(key, None)


def _install_fake_sdk() -> MagicMock:
    """Replace ``sentry_sdk`` in ``sys.modules`` with a recording stub.

    Returns the fake ``.init`` MagicMock so tests can assert on the call.
    Also installs ``django`` and ``celery`` integration modules with real
    sentinel classes so ``_build_sentry_options`` can import them.
    """
    init_mock = MagicMock(name='sentry_sdk.init')
    fake_root = types.ModuleType('sentry_sdk')
    fake_root.init = init_mock
    fake_root.Dsn = MagicMock()

    # fake sentry_sdk.integrations.django.DjangoIntegration
    fake_django_mod = types.ModuleType('sentry_sdk.integrations.django')

    class DjangoIntegration:
        pass

    fake_django_mod.DjangoIntegration = DjangoIntegration

    # fake sentry_sdk.integrations.celery.CeleryIntegration
    fake_celery_mod = types.ModuleType('sentry_sdk.integrations.celery')

    class CeleryIntegration:
        pass

    fake_celery_mod.CeleryIntegration = CeleryIntegration

    fake_pkg = types.ModuleType('sentry_sdk.integrations')

    class _FakePackage:
        django = fake_django_mod
        celery = fake_celery_mod

    fake_pkg.__path__ = []  # mark as package
    fake_integrations_pkg = types.ModuleType('sentry_sdk.integrations')
    fake_integrations_pkg.__path__ = []
    fake_integrations_pkg.django = fake_django_mod
    fake_integrations_pkg.celery = fake_celery_mod
    fake_root.integrations = fake_integrations_pkg

    sys.modules['sentry_sdk'] = fake_root
    sys.modules['sentry_sdk.integrations'] = fake_integrations_pkg
    sys.modules['sentry_sdk.integrations.django'] = fake_django_mod
    sys.modules['sentry_sdk.integrations.celery'] = fake_celery_mod
    return init_mock


def _uninstall_fake_sdk():
    for name in (
        'sentry_sdk',
        'sentry_sdk.integrations',
        'sentry_sdk.integrations.django',
        'sentry_sdk.integrations.celery',
    ):
        sys.modules.pop(name, None)


class InitSentryDisabledTests(SimpleTestCase):
    """init_sentry is a no-op when SENTRY_DSN is unset."""

    def setUp(self):
        _clear_sentry_env()
        reset_sentry_for_tests()
        # Ensure no stale fake SDK lingers.
        _uninstall_fake_sdk()

    def tearDown(self):
        _clear_sentry_env()
        reset_sentry_for_tests()
        _uninstall_fake_sdk()

    def test_returns_false_when_dsn_unset(self):
        self.assertFalse(init_sentry())

    def test_does_not_import_sentry_sdk(self):
        """init_sentry must not even touch sentry_sdk when DSN is unset."""
        import builtins
        real_import = builtins.__import__

        def _guard(name, *args, **kwargs):
            if name == 'sentry_sdk' or name.startswith('sentry_sdk.'):
                raise AssertionError(
                    'sentry_sdk must not be imported when SENTRY_DSN is unset'
                )
            return real_import(name, *args, **kwargs)

        with patch('builtins.__import__', side_effect=_guard):
            self.assertFalse(init_sentry())

    def test_manage_py_safe_to_import_without_dsn(self):
        """manage.py imports init_sentry at module level (for mgmt cmd errors).

        When SENTRY_DSN is unset, importing and calling init_sentry from
        manage.py must be a no-op — no SDK import, no side effects.
        """
        # Re-import the sentry module to ensure a clean state.
        import importlib
        import ecommerce.sentry as sentry_mod
        importlib.reload(sentry_mod)
        # init_sentry should not raise and should return False.
        self.assertFalse(sentry_mod.init_sentry())


class InitSentryIdempotencyTests(SimpleTestCase):
    """init_sentry calls sentry_sdk.init exactly once per process."""

    def setUp(self):
        _clear_sentry_env()
        os.environ['SENTRY_DSN'] = 'https://x@o0.ingest.sentry.io/0'
        reset_sentry_for_tests()
        self.init_mock = _install_fake_sdk()

    def tearDown(self):
        _clear_sentry_env()
        reset_sentry_for_tests()
        _uninstall_fake_sdk()

    def test_init_called_once_then_noop(self):
        first = init_sentry()
        self.assertTrue(first)
        self.init_mock.assert_called_once()
        # Second call is a no-op — the SDK is not re-initialised.
        second = init_sentry()
        self.assertTrue(second)
        self.assertEqual(self.init_mock.call_count, 1)

    def test_init_wired_with_config(self):
        init_sentry()
        kwargs = self.init_mock.call_args.kwargs
        self.assertEqual(kwargs['dsn'], 'https://x@o0.ingest.sentry.io/0')
        # Integrations list contains the Django and Celery integrations.
        integ_types = [type(i).__name__ for i in kwargs['integrations']]
        self.assertIn('DjangoIntegration', integ_types)
        self.assertIn('CeleryIntegration', integ_types)
        # before_send / before_breadcrumb are wired for scrubbing.
        self.assertIn('before_send', kwargs)
        self.assertIn('before_breadcrumb', kwargs)
        # request_bodies is set so we control what's sent.
        self.assertEqual(kwargs['request_bodies'], 'always')

    def test_release_environment_passed_through(self):
        with patch.dict(os.environ, {
            'SENTRY_DSN': 'https://x@o0.ingest.sentry.io/0',
            'SENTRY_ENVIRONMENT': 'staging',
            'SENTRY_RELEASE': 'v1.0.0',
            'SENTRY_TRACES_SAMPLE_RATE': '0.5',
            'SENTRY_SEND_DEFAULT_PII': 'false',
        }):
            init_sentry()
        kwargs = self.init_mock.call_args.kwargs
        self.assertEqual(kwargs['environment'], 'staging')
        self.assertEqual(kwargs['release'], 'v1.0.0')
        self.assertEqual(kwargs['traces_sample_rate'], 0.5)
        self.assertFalse(kwargs['send_default_pii'])


class InitSentryMissingSdkTests(SimpleTestCase):
    """When sentry-sdk is not installed but DSN is set, startup survives."""

    def setUp(self):
        _clear_sentry_env()
        os.environ['SENTRY_DSN'] = 'https://x@o0.ingest.sentry.io/0'
        reset_sentry_for_tests()
        _uninstall_fake_sdk()

    def tearDown(self):
        _clear_sentry_env()
        reset_sentry_for_tests()
        _uninstall_fake_sdk()

    def test_returns_false_and_warns_when_sdk_missing(self):
        import builtins
        real_import = builtins.__import__

        def _no_sdk(name, *args, **kwargs):
            if name == 'sentry_sdk' or name.startswith('sentry_sdk.'):
                raise ImportError('No module named sentry_sdk')
            return real_import(name, *args, **kwargs)

        with self.assertLogs('ecommerce.sentry', level='WARNING') as logs:
            with __import__('unittest.mock', fromlist=['patch']).patch(
                'builtins.__import__', side_effect=_no_sdk
            ):
                result = init_sentry()
        self.assertFalse(result)
        self.assertTrue(
            any('sentry-sdk is not installed' in line for line in logs.output),
            f'expected a warning about missing SDK in {logs.output}',
        )


class InitSentryOptionsTests(SimpleTestCase):
    """_build_sentry_options translates the config into init kwargs."""

    def setUp(self):
        _clear_sentry_env()
        _install_fake_sdk()

    def tearDown(self):
        _clear_sentry_env()
        _uninstall_fake_sdk()

    def test_full_options(self):
        from ecommerce.sentry import _build_sentry_options
        from ecommerce.sentry_config import SentryConfig
        cfg = SentryConfig(
            dsn='https://key@o0.ingest.sentry.io/0',
            environment='test',
            release='test-release',
            traces_sample_rate=0.2,
            profiles_sample_rate=0.0,
            send_default_pii=False,
            scrub_request_headers=True,
            scrub_request_cookies=True,
            scrub_payment_data=True,
            sensitive_fields=('password', 'cvv'),
            celery_enabled=True,
            django_enabled=True,
            options={},
        )
        options = _build_sentry_options(cfg)
        self.assertEqual(options['dsn'], cfg.dsn)
        self.assertEqual(options['environment'], cfg.environment)
        self.assertEqual(options['release'], cfg.release)
        self.assertEqual(options['traces_sample_rate'], 0.2)
        self.assertEqual(options['send_default_pii'], False)
        self.assertEqual(options['request_bodies'], 'always')

    def test_profiles_only_when_positive(self):
        from ecommerce.sentry import _build_sentry_options
        from ecommerce.sentry_config import SentryConfig
        cfg = SentryConfig(
            dsn='https://key@o0.ingest.sentry.io/0', environment='t',
            release='r', traces_sample_rate=0.2, profiles_sample_rate=1.0,
            send_default_pii=False, scrub_request_headers=True,
            scrub_request_cookies=True, scrub_payment_data=True,
            sensitive_fields=(), celery_enabled=True, django_enabled=True, options={},
        )
        options = _build_sentry_options(cfg)
        self.assertEqual(options['profiles_sample_rate'], 1.0)

        # A config with profiles off should omit the key entirely.
        cfg_off = SentryConfig(
            dsn='https://key@o0.ingest.sentry.io/0', environment='t',
            release='r', traces_sample_rate=0.2, profiles_sample_rate=0.0,
            send_default_pii=False, scrub_request_headers=True,
            scrub_request_cookies=True, scrub_payment_data=True,
            sensitive_fields=(), celery_enabled=True, django_enabled=True, options={},
        )
        options_off = _build_sentry_options(cfg_off)
        self.assertNotIn('profiles_sample_rate', options_off)

    def test_no_integrations_when_both_off(self):
        from ecommerce.sentry import _build_sentry_options
        from ecommerce.sentry_config import SentryConfig
        cfg = SentryConfig(
            dsn='https://key@o0.ingest.sentry.io/0', environment='t',
            release='r', traces_sample_rate=0.2, profiles_sample_rate=0.0,
            send_default_pii=False, scrub_request_headers=True,
            scrub_request_cookies=True, scrub_payment_data=True,
            sensitive_fields=(), celery_enabled=False, django_enabled=False, options={},
        )
        options = _build_sentry_options(cfg)
        self.assertNotIn('integrations', options)

    def test_only_celery_integration(self):
        from ecommerce.sentry import _build_sentry_options
        from ecommerce.sentry_config import SentryConfig
        cfg = SentryConfig(
            dsn='https://key@o0.ingest.sentry.io/0', environment='t',
            release='r', traces_sample_rate=0.2, profiles_sample_rate=0.0,
            send_default_pii=False, scrub_request_headers=True,
            scrub_request_cookies=True, scrub_payment_data=True,
            sensitive_fields=(), celery_enabled=True, django_enabled=False, options={},
        )
        options = _build_sentry_options(cfg)
        names = [type(i).__name__ for i in options['integrations']]
        self.assertIn('CeleryIntegration', names)
        self.assertNotIn('DjangoIntegration', names)

    def test_only_django_integration(self):
        from ecommerce.sentry import _build_sentry_options
        from ecommerce.sentry_config import SentryConfig
        cfg = SentryConfig(
            dsn='https://key@o0.ingest.sentry.io/0', environment='t',
            release='r', traces_sample_rate=0.2, profiles_sample_rate=0.0,
            send_default_pii=False, scrub_request_headers=True,
            scrub_request_cookies=True, scrub_payment_data=True,
            sensitive_fields=(), celery_enabled=False, django_enabled=True, options={},
        )
        options = _build_sentry_options(cfg)
        names = [type(i).__name__ for i in options['integrations']]
        self.assertNotIn('CeleryIntegration', names)
        self.assertIn('DjangoIntegration', names)

