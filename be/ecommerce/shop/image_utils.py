"""Image optimization utilities for product thumbnails (card 86ac598h).

Generates thumbnail variants on demand using Pillow's ``ImageOps.fit``,
stores them under ``<upload_dir>/<size_key>/`` in the same storage as the
original, and exposes URLs + srcset lists for the template layer.

Thumbnail sizes are declared in ``THUMBNAIL_SIZES``.
"""
from __future__ import annotations

from functools import lru_cache

from django.conf import settings

try:
    from PIL import Image, ImageOps
except ImportError:  # Pillow is a declared dependency; guard for clarity.
    Image = None
    ImageOps = None

# (width, height, crop_to_fit) — sizes are square crops for product images.
THUMBNAIL_SIZES = {
    'thumb': (300, 300),
    'medium': (600, 600),
    'large': (1200, 1200),
}


def _validate_pillow():
    """Fail fast with a clear message if Pillow is unavailable."""
    if Image is None or ImageOps is None:
        raise RuntimeError(
            'Pillow is required for image thumbnails but is not installed.'
        )


@lru_cache(maxsize=1)
def get_thumbnail_sizes():
    """Return the thumbnail size map (allows settings override if needed).

    Defaults come from ``THUMBNAIL_SIZES`` above.
    """
    return THUMBNAIL_SIZES


def _thumbnail_subdir(image_field, size_key):
    """Relative dir path: ``<upload_dir>/<size_key>/``.

    ``image_field.name`` is ``products/%Y/%m/%d/<file>``; we take the
    directory portion and append ``<size_key>/``.
    """
    import os

    upload_dir = os.path.dirname(image_field.name)
    return f'{upload_dir}/{size_key}/'


def _thumbnail_filename(image_field, size_key):
    """Relative filename for a thumbnail: ``<thumbsubdir><basename>``."""
    import os

    base = os.path.basename(image_field.name)
    return f'{_thumbnail_subdir(image_field, size_key)}{base}'


def generate_thumbnails(image_field, sizes=None):
    """Generate thumbnail variants for an ``ImageField`` (on disk).

    Uses ``ImageOps.fit`` to crop-resample each variant to the target size.
    Variants are written back through the field's storage so they are served
    by the same media pipeline as the original. Existing variants are skipped
    (idempotent).

    Args:
        image_field: A Django ``ImageField`` (e.g. ``product.image``).
        sizes: optional mapping of ``size_key -> (width, height)``.
            Defaults to ``THUMBNAIL_SIZES``.

    Returns:
        dict mapping ``size_key`` to the relative thumbnail name.

    May return ``{}`` if the field has no file attached.
    """
    _validate_pillow()
    if not image_field or not image_field.name:
        return {}

    if sizes is None:
        sizes = get_thumbnail_sizes()

    generated = {}
    # ``image_field.path`` works when the storage is the local filesystem
    # (the only backend used by this project). ``Image.open`` accepts a path
    # or a file object.
    path = image_field.path
    with Image.open(path) as img:
        for size_key, (width, height) in sizes.items():
            thumb_name = _thumbnail_filename(image_field, size_key)
            # Skip if already present in storage.
            if image_field.storage.exists(thumb_name):
                generated[size_key] = thumb_name
                continue

            thumb = ImageOps.fit(img, (width, height), method=Image.LANCZOS)

            # Write through the storage backend so the file lands under the
            # same MEDIA_ROOT + upload_to convention as the original.
            from io import BytesIO
            from django.core.files.base import ContentFile

            buf = BytesIO()
            thumb.save(buf, format='JPEG', quality=85)
            buf.seek(0)
            image_field.storage.save(
                thumb_name,
                ContentFile(buf.getvalue()),
            )
            generated[size_key] = thumb_name
    return generated


def _ensure_thumbnail(image_field, size_key):
    """Generate a single thumbnail on demand if missing, return its name."""
    _validate_pillow()
    if not image_field or not image_field.name:
        return None

    sizes = get_thumbnail_sizes()
    if size_key not in sizes:
        return None

    thumb_name = _thumbnail_filename(image_field, size_key)
    if not image_field.storage.exists(thumb_name):
        generate_thumbnails(image_field, {size_key: sizes[size_key]})
    return thumb_name


def get_thumbnail_url(image_field, size_key):
    """Return the URL to a thumbnail, generating it on demand.

    Returns ``None`` if the image field has no file or the size key is unknown.
    """
    if not image_field or not image_field.name:
        return None

    sizes = get_thumbnail_sizes()
    if size_key not in sizes:
        return None

    thumb_name = _ensure_thumbnail(image_field, size_key)
    if thumb_name is None:
        return None
    return image_field.storage.url(thumb_name)


def get_srcset_urls(image_field):
    """Return ``[(url, width), ...]`` for every thumbnail size.

    Suitable for use as an ``srcset`` attribute on an ``<img>``.
    """
    if not image_field or not image_field.name:
        return []

    sizes = get_thumbnail_sizes()
    pairs = []
    for size_key, (width, _height) in sizes.items():
        url = get_thumbnail_url(image_field, size_key)
        if url:
            pairs.append((url, width))
    return pairs


def cleanup_thumbnails(image_field):
    """Remove all generated thumbnails for ``image_field``.

    Used by signals when a product image is replaced or removed.
    """
    if not image_field or not image_field.name:
        return

    sizes = get_thumbnail_sizes()
    for size_key in sizes:
        thumb_name = _thumbnail_filename(image_field, size_key)
        if image_field.storage.exists(thumb_name):
            try:
                image_field.storage.delete(thumb_name)
            except Exception:
                # Never let thumbnail cleanup break a model save/delete.
                pass
