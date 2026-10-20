"""Tests for image optimization / thumbnail generation (card 86ac598h).

Covers:
1. ``generate_thumbnails`` writes variants at the correct on-disk paths.
2. ``get_thumbnail_url`` resolves URLs and generates on demand.
3. ``get_srcset_urls`` returns ``[(url, width), ...]`` for every size.
4. ``_ProductProxy`` exposes ``thumbnail_url`` and ``srcset_urls`` matching
   the cached dict serialization path used by the list view.
"""
import os
import shutil
import tempfile
from decimal import Decimal
from io import BytesIO

from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from PIL import Image

from shop.image_utils import (
    THUMBNAIL_SIZES,
    cleanup_thumbnails,
    generate_thumbnails,
    get_srcset_urls,
    get_thumbnail_url,
)
from shop.models import Category, Product
from shop.views.product_list_view import (
    _ProductProxy,
    _product_to_dict,
)


def build_test_jpeg(size=(600, 600)):
    """Deterministic JPEG of the given size for thumbnail generation."""
    img = Image.new('RGB', size, (200, 120, 40))
    buf = BytesIO()
    img.save(buf, format='JPEG', quality=90)
    return ContentFile(buf.getvalue(), name='test-product.jpg')


class ProductMediaFixture(TestCase):
    """Shared fixture: isolated MEDIA_ROOT per test."""

    def setUp(self):
        self.media_root = tempfile.mkdtemp(prefix='image-opt-test-')
        self.addCleanup(shutil.rmtree, self.media_root, True)
        self.media_override = override_settings(MEDIA_ROOT=self.media_root)
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)

        self.category = Category.objects.create(name='Home', slug='home')
        self.product = Product.objects.create(
            category=self.category,
            name='Test Product',
            slug='test-product',
            price=Decimal('42.00'),
        )
        self.product.image.save('test-product.jpg', build_test_jpeg(), save=True)

    def thumbnail_disk_path(self, size_key):
        """Absolute on-disk path a thumbnail *should* live at, for assertions."""
        upload_dir = os.path.dirname(self.product.image.name)
        return os.path.join(
            self.media_root, upload_dir, size_key, 'test-product.jpg'
        )

    def thumb_relative_name(self, size_key):
        """Relative storage name a thumbnail should have."""
        upload_dir = os.path.dirname(self.product.image.name)
        return f'{upload_dir}/{size_key}/test-product.jpg'


class ThumbnailGenerationTests(ProductMediaFixture):
    def test_generate_thumbnails_creates_files_at_correct_paths(self):
        generated = generate_thumbnails(self.product.image)
        self.assertEqual(set(generated.keys()), set(THUMBNAIL_SIZES.keys()))
        for size_key in THUMBNAIL_SIZES:
            self.assertTrue(
                self.product.image.storage.exists(generated[size_key]),
                f'thumbnail {size_key} not found in storage',
            )
            self.assertTrue(
                os.path.exists(self.thumbnail_disk_path(size_key)),
                f'thumbnail {size_key} not found on disk',
            )

    def test_generate_thumbnails_is_idempotent(self):
        """A second call must not regenerate existing thumbnails."""
        generate_thumbnails(self.product.image)
        first_mtime = os.path.getmtime(self.thumbnail_disk_path('thumb'))
        generate_thumbnails(self.product.image)
        second_mtime = os.path.getmtime(self.thumbnail_disk_path('thumb'))
        self.assertEqual(first_mtime, second_mtime)

    def test_generate_thumbnails_skips_empty_field(self):
        product = Product.objects.create(
            category=self.category,
            name='No Image',
            slug='no-image',
            price=Decimal('10.00'),
        )
        self.assertEqual(generate_thumbnails(product.image), {})


class ThumbnailUrlTests(ProductMediaFixture):
    def test_get_thumbnail_url_resolves_and_generates_on_demand(self):
        # File must not exist yet.
        self.assertFalse(
            self.product.image.storage.exists(self.thumb_relative_name('thumb'))
        )
        url = get_thumbnail_url(self.product.image, 'thumb')
        self.assertIsNotNone(url)
        # On-demand generation: file should now exist in storage.
        self.assertTrue(
            self.product.image.storage.exists(self.thumb_relative_name('thumb'))
        )

    def test_get_thumbnail_url_unknown_size_returns_none(self):
        self.assertIsNone(get_thumbnail_url(self.product.image, 'bogus'))

    def test_get_thumbnail_url_empty_field_returns_none(self):
        product = Product.objects.create(
            category=self.category,
            name='No Image',
            slug='no-image-2',
            price=Decimal('10.00'),
        )
        self.assertIsNone(get_thumbnail_url(product.image, 'thumb'))

    def test_product_model_thumbnail_url_property(self):
        self.assertEqual(
            self.product.thumbnail_url,
            get_thumbnail_url(self.product.image, 'thumb'),
        )


class SrcsetTests(ProductMediaFixture):
    def test_srcset_returns_all_sizes(self):
        pairs = get_srcset_urls(self.product.image)
        self.assertEqual(len(pairs), len(THUMBNAIL_SIZES))
        widths = [w for _url, w in pairs]
        for size_key, (width, _h) in THUMBNAIL_SIZES.items():
            self.assertIn(width, widths)

    def test_srcset_pairs_are_url_and_int(self):
        pairs = get_srcset_urls(self.product.image)
        for url, width in pairs:
            self.assertTrue(url.startswith('/'), url)
            self.assertIsInstance(width, int)

    def test_product_model_srcset_urls_property(self):
        pairs = self.product.srcset_urls
        self.assertEqual(len(pairs), len(THUMBNAIL_SIZES))

    def test_srcset_empty_field_returns_empty(self):
        product = Product.objects.create(
            category=self.category,
            name='No Image 3',
            slug='no-image-3',
            price=Decimal('10.00'),
        )
        self.assertEqual(get_srcset_urls(product.image), [])
        self.assertEqual(product.srcset_urls, [])


class CleanupThumbnailsTests(ProductMediaFixture):
    def test_cleanup_removes_generated_thumbnails(self):
        generate_thumbnails(self.product.image)
        for size_key in THUMBNAIL_SIZES:
            self.assertTrue(
                self.product.image.storage.exists(
                    self.thumb_relative_name(size_key)
                )
            )
        cleanup_thumbnails(self.product.image)
        for size_key in THUMBNAIL_SIZES:
            self.assertFalse(
                self.product.image.storage.exists(
                    self.thumb_relative_name(size_key)
                )
            )


class ProductProxyTests(ProductMediaFixture):
    def _to_proxy(self, product):
        """Round-trip a product through the dict + proxy pipeline."""
        d = _product_to_dict(product)
        return _ProductProxy(
            id=d['id'],
            name=d['name'],
            slug=d['slug'],
            price=d['price'],
            image_url=d['image_url'],
            thumbnail_url=d['thumbnail_url'],
            srcset_urls=d['srcset_urls'],
        )

    def test_product_proxy_has_thumbnail_url(self):
        proxy = self._to_proxy(self.product)
        self.assertIsNotNone(proxy.thumbnail_url)

    def test_product_proxy_srcset_urls(self):
        proxy = self._to_proxy(self.product)
        self.assertEqual(len(proxy.srcset_urls), len(THUMBNAIL_SIZES))

    def test_product_to_dict_includes_thumbnail_and_srcset(self):
        d = _product_to_dict(self.product)
        self.assertIn('thumbnail_url', d)
        self.assertIn('srcset_urls', d)
        self.assertTrue(d['thumbnail_url'])
        self.assertTrue(len(d['srcset_urls']) == len(THUMBNAIL_SIZES))

    def test_product_proxy_falls_back_gracefully_without_image(self):
        plain = Product.objects.create(
            category=self.category,
            name='Plain',
            slug='plain',
            price=Decimal('5.00'),
        )
        d = _product_to_dict(plain)
        proxy = _ProductProxy(
            id=d['id'],
            name=d['name'],
            slug=d['slug'],
            price=d['price'],
            image_url=d['image_url'],
            thumbnail_url=d['thumbnail_url'],
            srcset_urls=d['srcset_urls'],
        )
        self.assertIsNone(proxy.thumbnail_url)
        self.assertEqual(proxy.srcset_urls, [])
