"""Optional CDN-backed static asset delivery (card 327eeba2 — Phase 3.1 caching).

The helper mirrors the project's existing env-driven config pattern
(``db_config`` / ``cache_config`` / ``social_auth_config``): it inspects the
environment and returns either ``None`` (CDN off, fall back to local
``/static/``) or a frozen config object describing the CDN URL and whether to
use Django's manifest storage for cache-busting versioned filenames.

Design rules
------------
* **Optional — never forced.** An unset or empty ``STATIC_CDN_URL`` means
  "serve static files from the app itself" (via WhiteNoise, as today). No CDN
  credentials, buckets, or third-party packages are required to run locally or
  in tests.
* **No secrets in the repo.** The CDN is purely a public URL prefix — objects
  on a static CDN are public by definition. There is nothing to leak.
* **Manifest versioning.** When ``STATIC_CDN_URL`` is set the manifest
  storage (``ManifestStaticFilesStorage``) is selected so ``collectstatic``
  emits ``<name>.<hash>.ext`` filenames; ``{% static %}`` then resolves to the
  hashed URL, giving immutable cache headers on the CDN edge. When unset the
  WhiteNoise compressed storage is kept (it already gzips/Brotli-caches in
  memory and works without a manifest, which is fine for local dev).
* **Media never goes to the CDN.** ``MEDIA_URL`` stays ``/media/`` and is
  served from the app / nginx volume — uploaded product images are private
  user content and must not be cached publicly on a CDN edge.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlsplit


@dataclass(frozen=True)
class StaticCDNConfig:
    """Wiring for a public CDN serving collected static assets.

    ``url`` — the base URL the browser fetches static assets from. Must be
    schemed (``https://`` or ``//``) and must end with a trailing ``/`` so
    ``urljoin`` behaves predictably; ``{% static 'css/base.css' %}`` becomes
    ``<url>css/base.css``.

    ``manifest`` — whether Django's ``ManifestStaticFilesStorage`` is active.
    When True ``collectstatic`` writes ``staticfiles.json`` and rewrites
    references to hash-named files (e.g. ``css/base.<hash>.css``), which is what
    makes CDN cache headers safe to set to a very long ``max-age``. It defaults
    to True whenever a CDN URL is set (immutable assets on the edge); set
    ``STATIC_CDN_MANIFEST=false`` to keep WhiteNoise's
    ``CompressedManifestStaticFilesStorage`` instead. In that mode settings
    also sets ``WHITENOISE_MANIFEST_STRICT = False``, so a reference missing
    from the manifest is hashed on demand from the file rather than raising.
    """

    url: str
    manifest: bool

    @property
    def origin(self) -> str:
        """CSP source expression for the CDN origin (no path, no trailing /).

        ``https://cdn.example.com/assets/`` → ``https://cdn.example.com``;
        a protocol-relative ``//cdn.example.com/`` has no scheme, so it becomes
        the scheme-less host-source ``cdn.example.com`` (CSP resolves it against
        the page scheme).
        """
        parts = urlsplit(self.url)
        if parts.scheme:
            return f'{parts.scheme}://{parts.netloc}'
        return parts.netloc

    @property
    def storage_backend(self) -> str:
        """Django STORAGES['staticfiles'] backend for this mode."""
        if self.manifest:
            return 'django.contrib.staticfiles.storage.ManifestStaticFilesStorage'
        return 'whitenoise.storage.CompressedManifestStaticFilesStorage'


def _is_cdn_url(value: str) -> bool:
    """A CDN URL must be a non-empty, schemed URL ending in ``/``."""
    if not value:
        return False
    value = value.strip()
    if not value.startswith(('http://', 'https://', '//')):
        return False
    return value.endswith('/')


def static_cdn_config(env: Mapping[str, str]) -> StaticCDNConfig | None:
    """Return the CDN config, or ``None`` when the CDN is disabled.

    ``env`` maps variable names to strings (normally ``os.environ``).

    * ``STATIC_CDN_URL`` set + non-empty → CDN on, manifest storage on.
    * ``STATIC_CDN_URL`` unset or empty → CDN off, WhiteNoise storage (local).
    * ``STATIC_CDN_MANIFEST`` (optional) selects the manifest storage backend:
      left unset/truthy → Django's strict ``ManifestStaticFilesStorage``;
      ``false``/``0``/``no``/``off`` → WhiteNoise's compressed-manifest
      storage (relaxed to non-strict in settings).
    """
    cdn_url = (env.get('STATIC_CDN_URL', '') or '').strip()
    if not _is_cdn_url(cdn_url):
        return None

    manifest_raw = (env.get('STATIC_CDN_MANIFEST', '') or '').strip().lower()
    if manifest_raw in ('0', 'false', 'no', 'off'):
        manifest = False
    else:
        # Default: manifest ON when a CDN URL is present, so assets are
        # cache-busted / immutable on the edge.
        manifest = True

    return StaticCDNConfig(url=cdn_url, manifest=manifest)
