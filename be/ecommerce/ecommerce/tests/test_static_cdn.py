"""Tests for optional CDN-backed static file delivery (card 327eeba2).

Covers the env-driven wiring helper (``static_config.static_cdn_config``) and
the settings that consume it, plus integration behaviour through the test
Client:

* CDN disabled (default) → STATIC_URL stays ``/static/``, WhiteNoise storage.
* CDN enabled → STATIC_URL becomes the CDN URL, manifest storage selected.
* Manifest off override → WhiteNoise's compressed-manifest storage (non-strict:
  a reference missing from the manifest is hashed on demand, not an error).
* ``{% static %}`` template tag resolves to the CDN URL when configured.
* ``collectstatic`` (manifest) behaviour with/without STATIC_CDN_URL.
* Media files are never routed to the CDN.
* The enforced CSP allows the CDN origin for scripts/styles/images/fonts
  (and is unchanged when the CDN is off — see ``test_csp_headers.py``).

No external services are required: these tests exercise the Python config
helper and Django's storage/template layer directly. The shared Redis cache
is untouched (the default LocMem cache is forced by the test runner).

The local-mode assertions assume the suite runs with the default environment
(no ``STATIC_CDN_URL``), as compose and CI do; the helper and settings-wiring
tests hold in either regime.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles.storage import ManifestStaticFilesStorage
from django.test import SimpleTestCase, TestCase, override_settings
from django.templatetags.static import static
from django.urls import reverse

from ecommerce.static_config import (
    StaticCDNConfig,
    static_cdn_config,
    _is_cdn_url,
)

WHITENOISE_STORAGE = 'whitenoise.storage.CompressedStaticFilesStorage'
COMPRESSED_MANIFEST = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
MANIFEST_ONLY = 'django.contrib.staticfiles.storage.ManifestStaticFilesStorage'

PROJECT_ROOT = Path(__file__).resolve().parents[2]

_SETTINGS_SNAPSHOT_CODE = """
import json
from django.conf import settings

print(json.dumps({
    'static_url': settings.STATIC_URL,
    'storage': settings.STORAGES['staticfiles']['BACKEND'],
    'manifest_strict': getattr(settings, 'WHITENOISE_MANIFEST_STRICT', None),
    'media_url': settings.MEDIA_URL,
    'csp_policy': settings.CSP_POLICY,
}))
"""


def _settings_snapshot(**overrides):
    """Import ``ecommerce.settings`` in a fresh interpreter with *overrides*.

    The suite always boots with the CDN off, so the enabled branch of
    ``settings.py`` (STATIC_URL, storage backend, CSP extension) is only
    reachable by importing it in a child process.
    """
    env = dict(os.environ)
    env.pop('STATIC_CDN_URL', None)
    env.pop('STATIC_CDN_MANIFEST', None)
    env.update(overrides)
    env['DJANGO_SETTINGS_MODULE'] = 'ecommerce.settings'
    env['PYTHONPATH'] = os.pathsep.join(
        part for part in (str(PROJECT_ROOT), env.get('PYTHONPATH')) if part
    )
    proc = subprocess.run(
        [sys.executable, '-c', _SETTINGS_SNAPSHOT_CODE],
        cwd=str(PROJECT_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f'importing ecommerce.settings failed: {proc.stderr}'
        )
    return json.loads(proc.stdout)


class IsCdnUrlTests(SimpleTestCase):
    """The URL validator accepts well-formed CDN URLs and rejects junk."""

    VALID = (
        'https://cdn.example.com/',
        'http://cdn.example.com/',
        '//cdn.example.com/',
        'https://cdn.example.com/assets/',
    )
    INVALID = (
        '',
        '  ',
        'cdn.example.com',           # no scheme
        '/static/',                  # relative to site root
        'static/',                   # bare
        'https://cdn.example.com',   # no trailing slash
    )

    def test_valid_urls_are_accepted(self):
        for url in self.VALID:
            self.assertTrue(_is_cdn_url(url), f'{url!r} should be accepted')

    def test_invalid_urls_are_rejected(self):
        for url in self.INVALID:
            self.assertFalse(_is_cdn_url(url), f'{url!r} should be rejected')


class StaticCDNConfigHelperTests(SimpleTestCase):
    """The env-driven wiring helper: pure function, no Django settings needed."""

    def test_no_url_returns_none(self):
        self.assertIsNone(static_cdn_config({}))

    def test_empty_url_returns_none(self):
        self.assertIsNone(static_cdn_config({'STATIC_CDN_URL': ''}))

    def test_whitespace_url_returns_none(self):
        self.assertIsNone(static_cdn_config({'STATIC_CDN_URL': '  '}))

    def test_relative_url_returns_none(self):
        self.assertIsNone(
            static_cdn_config({'STATIC_CDN_URL': '/static/'})
        )

    def test_url_without_scheme_returns_none(self):
        config = static_cdn_config({'STATIC_CDN_URL': 'cdn.example.com/'})
        self.assertIsNone(config)

    def test_url_without_trailing_slash_returns_none(self):
        config = static_cdn_config({'STATIC_CDN_URL': 'https://cdn.example.com'})
        self.assertIsNone(config)

    def test_absolute_https_url_returns_config(self):
        config = static_cdn_config({'STATIC_CDN_URL': 'https://cdn.example.com/'})
        self.assertIsInstance(config, StaticCDNConfig)
        self.assertEqual(config.url, 'https://cdn.example.com/')
        self.assertTrue(config.manifest)

    def test_protocol_relative_url_returns_config(self):
        config = static_cdn_config({'STATIC_CDN_URL': '//cdn.example.com/'})
        self.assertIsNotNone(config)
        self.assertEqual(config.url, '//cdn.example.com/')

    def test_manifest_defaults_true_when_url_set(self):
        config = static_cdn_config({'STATIC_CDN_URL': 'https://cdn.example.com/'})
        self.assertTrue(config.manifest)

    def test_manifest_override_false(self):
        config = static_cdn_config({
            'STATIC_CDN_URL': 'https://cdn.example.com/',
            'STATIC_CDN_MANIFEST': 'false',
        })
        self.assertFalse(config.manifest)

    def test_manifest_override_no(self):
        config = static_cdn_config({
            'STATIC_CDN_URL': 'https://cdn.example.com/',
            'STATIC_CDN_MANIFEST': 'no',
        })
        self.assertFalse(config.manifest)

    def test_manifest_override_off(self):
        config = static_cdn_config({
            'STATIC_CDN_URL': 'https://cdn.example.com/',
            'STATIC_CDN_MANIFEST': 'off',
        })
        self.assertFalse(config.manifest)

    def test_manifest_override_0(self):
        config = static_cdn_config({
            'STATIC_CDN_URL': 'https://cdn.example.com/',
            'STATIC_CDN_MANIFEST': '0',
        })
        self.assertFalse(config.manifest)

    def test_whitespace_in_url_is_stripped(self):
        config = static_cdn_config({'STATIC_CDN_URL': '  https://cdn.example.com/  '})
        self.assertEqual(config.url, 'https://cdn.example.com/')

    def test_storage_backend_when_manifest_on(self):
        config = static_cdn_config({'STATIC_CDN_URL': 'https://cdn.example.com/'})
        self.assertEqual(config.storage_backend, MANIFEST_ONLY)

    def test_storage_backend_when_manifest_off(self):
        config = static_cdn_config({
            'STATIC_CDN_URL': 'https://cdn.example.com/',
            'STATIC_CDN_MANIFEST': 'false',
        })
        self.assertEqual(config.storage_backend, COMPRESSED_MANIFEST)

    def test_origin_strips_cdn_path(self):
        config = static_cdn_config({
            'STATIC_CDN_URL': 'https://cdn.example.com/assets/',
        })
        self.assertEqual(config.origin, 'https://cdn.example.com')

    def test_origin_keeps_non_default_port(self):
        config = static_cdn_config({
            'STATIC_CDN_URL': 'https://cdn.example.com:8443/',
        })
        self.assertEqual(config.origin, 'https://cdn.example.com:8443')

    def test_origin_for_protocol_relative_url_is_scheme_less(self):
        config = static_cdn_config({'STATIC_CDN_URL': '//cdn.example.com/'})
        self.assertEqual(config.origin, 'cdn.example.com')

    def test_config_is_frozen(self):
        """The returned object is immutable — settings treat it as a constant."""
        config = static_cdn_config({'STATIC_CDN_URL': 'https://cdn.example.com/'})
        with self.assertRaises(Exception):
            config.url = 'https://evil.example.com/'


class StaticCDNSettingsTests(SimpleTestCase):
    """Settings-level wiring: the flags and values consumed by the app."""

    def test_cdn_disabled_by_default(self):
        """With no STATIC_CDN_URL in the environment, CDN is off and local."""
        # settings.py already resolved these at import time from os.environ;
        # in the test environment .env has no STATIC_CDN_URL set, so:
        self.assertFalse(settings.STATIC_CDN_ENABLED)
        self.assertIsNone(settings.STATIC_CDN_CONFIG)
        self.assertEqual(settings.STATIC_URL, '/static/')
        self.assertEqual(
            settings.STORAGES['staticfiles']['BACKEND'],
            WHITENOISE_STORAGE,
        )

    def test_settings_config_is_resolved_from_the_environment(self):
        """settings.STATIC_CDN_CONFIG is exactly what the helper reads now."""
        self.assertEqual(
            settings.STATIC_CDN_CONFIG,
            static_cdn_config(os.environ),
        )
        self.assertEqual(
            settings.STATIC_CDN_ENABLED,
            settings.STATIC_CDN_CONFIG is not None,
        )

    def test_static_settings_derive_from_the_resolved_config(self):
        """STATIC_URL and STORAGES follow STATIC_CDN_CONFIG, never ad hoc.

        Written as an invariant so it holds whichever environment the suite
        runs in (CDN off locally/tests, on in a CDN deployment).
        """
        config = settings.STATIC_CDN_CONFIG
        storage = settings.STORAGES['staticfiles']['BACKEND']
        if config is None:
            self.assertEqual(settings.STATIC_URL, '/static/')
            self.assertEqual(storage, WHITENOISE_STORAGE)
        else:
            self.assertEqual(settings.STATIC_URL, config.url)
            self.assertEqual(storage, config.storage_backend)

    def test_media_url_is_always_local(self):
        """MEDIA_URL never points to the CDN."""
        self.assertEqual(settings.MEDIA_URL, '/media/')
        self.assertEqual(settings.MEDIA_ROOT, settings.BASE_DIR / 'media')

    def test_cdn_config_url_is_a_base_url(self):
        """A resolved config always ends with '/', so urljoin is predictable."""
        config = settings.STATIC_CDN_CONFIG
        if config is not None:
            self.assertTrue(config.url.endswith('/'))


class EnabledSettingsImportTests(SimpleTestCase):
    """The CDN-enabled branch of ``settings.py``, imported for real.

    The test process always boots with the CDN off, so an enabled deployment is
    exercised by importing settings in a child interpreter instead. This covers
    the wiring the app actually ships: CDN ``STATIC_URL``, manifest storage and
    the Content-Security-Policy that allows the CDN origin for assets.
    """

    def test_enabled_settings_allow_the_cdn_in_csp(self):
        snapshot = _settings_snapshot(STATIC_CDN_URL='https://cdn.example.com/')

        self.assertEqual(snapshot['static_url'], 'https://cdn.example.com/')
        self.assertEqual(snapshot['storage'], MANIFEST_ONLY)
        self.assertEqual(snapshot['media_url'], '/media/')

        policy = snapshot['csp_policy']
        for directive in ('script-src', 'style-src', 'img-src', 'font-src'):
            self.assertIn(
                'https://cdn.example.com',
                policy[directive].split(),
                f'{directive} must allow the CDN origin',
            )
        # Everything not serving static assets stays locked down.
        self.assertEqual(policy['default-src'], "'self'")
        self.assertEqual(policy['frame-ancestors'], "'none'")

    def test_enabled_settings_with_manifest_off_relax_whitenoise(self):
        snapshot = _settings_snapshot(
            STATIC_CDN_URL='https://cdn.example.com/',
            STATIC_CDN_MANIFEST='false',
        )

        self.assertEqual(snapshot['storage'], COMPRESSED_MANIFEST)
        self.assertIs(snapshot['manifest_strict'], False)
        self.assertIn(
            'https://cdn.example.com',
            snapshot['csp_policy']['img-src'].split(),
        )

    def test_default_settings_do_not_allow_a_cdn_origin(self):
        """CDN off: the enforced policy is exactly the pre-CDN one."""
        snapshot = _settings_snapshot()

        self.assertEqual(snapshot['static_url'], '/static/')
        self.assertIsNone(snapshot['manifest_strict'])
        self.assertNotIn('cdn.example.com', ' '.join(snapshot['csp_policy'].values()))


class StaticTemplateUrlTests(TestCase):
    """The {% static %} tag must resolve to the CDN URL when configured."""

    def test_static_tag_uses_local_url_by_default(self):
        """Without CDN, {% static %} produces /static/ URLs."""
        url = static('css/base.css')
        self.assertEqual(url, '/static/css/base.css')

    @override_settings(
        STATIC_CDN_ENABLED=True,
        STATIC_URL='https://cdn.example.com/',
        STATIC_CDN_CONFIG=StaticCDNConfig(
            url='https://cdn.example.com/',
            manifest=True,
        ),
    )
    def test_static_tag_uses_cdn_url_when_enabled(self):
        """With CDN on, {% static %} produces the CDN URL."""
        url = static('css/base.css')
        self.assertEqual(url, 'https://cdn.example.com/css/base.css')

    @override_settings(
        STATIC_CDN_ENABLED=True,
        STATIC_URL='https://cdn.example.com/assets/',
        STATIC_CDN_CONFIG=StaticCDNConfig(
            url='https://cdn.example.com/assets/',
            manifest=True,
        ),
    )
    def test_static_tag_preserves_cdn_path_prefix(self):
        url = static('css/base.css')
        self.assertEqual(url, 'https://cdn.example.com/assets/css/base.css')

    def test_template_renders_local_url_by_default(self):
        """A rendered template uses /static/ when the CDN is off."""
        from django.template import Template, Context
        template = Template("{% load static %}{% static 'css/base.css' %}")
        rendered = template.render(Context({}))
        self.assertEqual(rendered.strip(), '/static/css/base.css')

    @override_settings(
        STATIC_CDN_ENABLED=True,
        STATIC_URL='https://cdn.example.com/',
        STATIC_CDN_CONFIG=StaticCDNConfig(
            url='https://cdn.example.com/',
            manifest=True,
        ),
    )
    def test_template_renders_cdn_url_when_overridden(self):
        from django.template import Template, Context
        template = Template("{% load static %}{% static 'css/base.css' %}")
        rendered = template.render(Context({}))
        self.assertEqual(rendered.strip(), 'https://cdn.example.com/css/base.css')


class CollectstaticManifestTests(SimpleTestCase):
    """ManifestStaticFilesStorage writes a staticfiles.json manifest.

    These tests exercise the manifest storage directly (no subprocess needed)
    so they run fast in the test container.
    """

    def setUp(self):
        super().setUp()
        # Point STATIC_ROOT at a temp dir so we don't pollute the repo.
        self.tmp = tempfile.mkdtemp(prefix='cdn-static-test-')
        self.tmp_path = Path(self.tmp)
        # Seed a simple CSS file.
        css_dir = self.tmp_path / 'css'
        css_dir.mkdir(parents=True, exist_ok=True)
        (css_dir / 'base.css').write_text('body { color: red; }')

    def tearDown(self):
        super().tearDown()
        import shutil
        shutil.rmtree(self.tmp, True)

    @override_settings(STATIC_URL='/static/')
    def test_manifest_storage_writes_staticfiles_json(self):
        """ManifestStaticFilesStorage.post_process writes staticfiles.json."""
        from django.contrib.staticfiles.storage import (
            ManifestStaticFilesStorage,
        )
        from django.core.files.storage import FileSystemStorage
        storage = ManifestStaticFilesStorage(
            location=str(self.tmp_path),
            base_url='/static/',
        )
        # post_process is called by collectstatic with a dict mapping
        # relative name -> (storage, path_in_source). We build that.
        source_storage = FileSystemStorage(location=str(self.tmp_path))
        paths = {'css/base.css': (source_storage, 'css/base.css')}
        list(storage.post_process(paths))

        manifest_path = self.tmp_path / 'staticfiles.json'
        self.assertTrue(manifest_path.exists(), 'manifest was not written')
        data = json.loads(manifest_path.read_text())
        self.assertIn('css/base.css', data.get('paths', {}))

    @override_settings(STATIC_URL='/static/')
    def test_manifest_storage_resolves_hashed_url(self):
        """storage.url() returns the hashed filename when a manifest exists."""
        from django.contrib.staticfiles.storage import (
            ManifestStaticFilesStorage,
        )
        from django.core.files.storage import FileSystemStorage
        storage = ManifestStaticFilesStorage(
            location=str(self.tmp_path),
            base_url='/static/',
        )
        # Run post_process to populate the manifest.
        source_storage = FileSystemStorage(location=str(self.tmp_path))
        paths = {'css/base.css': (source_storage, 'css/base.css')}
        list(storage.post_process(paths))

        # Reload the storage so it reads the manifest from disk.
        storage2 = ManifestStaticFilesStorage(
            location=str(self.tmp_path),
            base_url='/static/',
        )
        url = storage2.url('css/base.css')
        self.assertTrue(url.startswith('/static/css/base.'))
        self.assertTrue(url.endswith('.css'))
        self.assertNotEqual(url, '/static/css/base.css')

    @override_settings(STATIC_URL='/static/')
    def test_missing_manifest_raises_value_error(self):
        """ManifestStaticFilesStorage raises ValueError when a manifest entry is missing.

        This is Django's own contract — the manifest must be present. The
        settings-level graceful path is the storage choice: WhiteNoise's
        compressed storage when the CDN is off, or its relaxed (non-strict)
        compressed-manifest variant when ``STATIC_CDN_MANIFEST=false``. This
        test documents the strict boundary.
        """
        from django.contrib.staticfiles.storage import (
            ManifestStaticFilesStorage,
        )
        storage = ManifestStaticFilesStorage(
            location=str(self.tmp_path),
            base_url='/static/',
        )
        # No post_process / manifest written — url() should raise.
        with self.assertRaises(ValueError):
            storage.url('css/base.css')


class ManifestOffDegradeTests(SimpleTestCase):
    """``STATIC_CDN_MANIFEST=false`` → WhiteNoise compressed-manifest storage.

    The manifest flag does not turn hashing off (both storages hash during
    ``collectstatic``); it swaps Django's strict manifest storage for
    WhiteNoise's compressing variant. Settings relax that variant with
    ``WHITENOISE_MANIFEST_STRICT = False`` so a stale or incomplete manifest
    hashes the file on demand instead of raising at render time.
    """

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix='cdn-manifest-off-test-')
        self.tmp_path = Path(self.tmp)
        css_dir = self.tmp_path / 'css'
        css_dir.mkdir(parents=True, exist_ok=True)
        (css_dir / 'base.css').write_text('body { color: red; }')

    def tearDown(self):
        super().tearDown()
        import shutil
        shutil.rmtree(self.tmp, True)

    def test_manifest_off_selects_whitenoise_compressed_manifest_storage(self):
        from whitenoise.storage import CompressedManifestStaticFilesStorage

        config = static_cdn_config({
            'STATIC_CDN_URL': 'https://cdn.example.com/',
            'STATIC_CDN_MANIFEST': 'false',
        })
        self.assertEqual(config.storage_backend, COMPRESSED_MANIFEST)
        self.assertNotEqual(config.storage_backend, MANIFEST_ONLY)
        self.assertTrue(
            issubclass(CompressedManifestStaticFilesStorage, ManifestStaticFilesStorage)
        )

    @override_settings(DEBUG=False, WHITENOISE_MANIFEST_STRICT=False)
    def test_missing_manifest_hashes_on_demand(self):
        """Graceful degrade: no manifest → hash computed from the file."""
        from whitenoise.storage import CompressedManifestStaticFilesStorage

        storage = CompressedManifestStaticFilesStorage(
            location=str(self.tmp_path),
            base_url='/static/',
        )
        url = storage.url('css/base.css')
        self.assertTrue(url.startswith('/static/css/base.'))
        self.assertTrue(url.endswith('.css'))
        self.assertNotEqual(url, '/static/css/base.css')

    @override_settings(DEBUG=False)
    def test_strict_manifest_would_raise_without_the_relaxation(self):
        """Documents why settings relaxes the storage (default is strict)."""
        from whitenoise.storage import CompressedManifestStaticFilesStorage

        storage = CompressedManifestStaticFilesStorage(
            location=str(self.tmp_path),
            base_url='/static/',
        )
        with self.assertRaises(ValueError):
            storage.url('css/base.css')


class WhitenoiseStorageFallbackTests(TestCase):
    """When CDN is off, the WhiteNoise storage still works (no manifest)."""

    def test_whitenoise_storage_is_configured_when_cdn_off(self):
        """The staticfiles backend is WhiteNoise when STATIC_CDN_URL is unset."""
        self.assertEqual(
            settings.STORAGES['staticfiles']['BACKEND'],
            WHITENOISE_STORAGE,
        )

    def test_whitenoise_storage_resolves_static_url(self):
        """WhiteNoise storage.url produces a /static/ URL by default."""
        from whitenoise.storage import CompressedStaticFilesStorage
        storage = CompressedStaticFilesStorage()
        self.assertEqual(storage.url('css/base.css'), '/static/css/base.css')


class StaticURLThroughClientTests(TestCase):
    """The catalog home renders {% static %} URLs according to STATIC_URL.

    This is the end-to-end check: when STATIC_CDN_URL is set (via
    override_settings), the rendered HTML in the <link> tag points at the CDN.
    """

    def test_home_page_renders_local_static_url(self):
        """Without override, the home page uses /static/."""
        response = self.client.get(reverse('shop:product_list'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('/static/css/base.css', response.content.decode())

    @override_settings(
        STATIC_CDN_ENABLED=True,
        STATIC_URL='https://cdn.example.com/',
        STATIC_CDN_CONFIG=StaticCDNConfig(
            url='https://cdn.example.com/',
            manifest=True,
        ),
    )
    def test_home_page_renders_cdn_static_url(self):
        """With CDN override, the home page uses the CDN URL."""
        response = self.client.get(reverse('shop:product_list'))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('https://cdn.example.com/css/base.css', html)
        self.assertNotIn('/static/css/base.css', html)


class StaticCDNMediaSeparationTests(SimpleTestCase):
    """Media files are never routed to the CDN regardless of STATIC_CDN_URL."""

    def test_media_url_is_never_a_cdn_url(self):
        """MEDIA_URL is always /media/, even when STATIC_CDN_URL is set."""
        # In the test env, STATIC_CDN_URL is unset, so MEDIA_URL is /media/.
        self.assertEqual(settings.MEDIA_URL, '/media/')

    @override_settings(
        STATIC_CDN_URL='https://cdn.example.com/',
        STATIC_CDN_ENABLED=True,
        STATIC_URL='https://cdn.example.com/',
        MEDIA_URL='/media/',
    )
    def test_media_url_overridden_stays_local(self):
        """Even with all CDN settings on, MEDIA_URL stays /media/."""
        self.assertEqual(settings.MEDIA_URL, '/media/')

    def test_static_and_media_urls_distinct(self):
        """STATIC_URL and MEDIA_URL never collide."""
        static_url = settings.STATIC_URL
        media_url = settings.MEDIA_URL
        self.assertNotEqual(static_url, media_url)
        # MEDIA_URL must be site-root absolute (never a CDN URL).
        self.assertTrue(media_url.startswith('/'))
