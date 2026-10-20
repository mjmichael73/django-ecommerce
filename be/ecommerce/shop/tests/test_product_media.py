"""Product image upload + media URL serving (card ga4e3hg5).

The card fixes two media gaps: the URL prefixes are now declared as site-root
absolute (``'/media/'``, ``'/static/'`` instead of ``'media/'``, ``'static/'``)
and the reverse-proxy config serves ``/media/`` and ``/static/`` from disk.

Note on Django's behaviour: ``django.conf.Settings.__getattr__`` runs
``_add_script_prefix()`` on ``MEDIA_URL``/``STATIC_URL``, so a *relative*
declared value still comes back as ``'/media/'`` at runtime. The rendered
``{{ product.image.url }}``/``{% static %}`` output was therefore already
site-root absolute on nested pages such as ``/<id>/<slug>/`` — the rendered-URL
tests below guard that contract, and ``test_url_prefixes_are_declared_absolute``
pins the declared settings values themselves (the actual edit this card makes).

The image field, the admin upload form, the seed images and the ``media_data``
volume already existed; this module proves the URLs they render are absolute.
"""
import importlib
import shutil
import tempfile
from decimal import Decimal
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import clear_url_caches, reverse
from PIL import Image

from shop.models import Category, Product

User = get_user_model()

JPEG_MAGIC = b'\xff\xd8'


def build_test_jpeg(name='test-product.jpg'):
    """Tiny deterministic JPEG — same local Pillow pattern as the seed command."""
    img = Image.new('RGB', (8, 8), (200, 120, 40))
    buf = BytesIO()
    img.save(buf, format='JPEG', quality=90)
    return ContentFile(buf.getvalue(), name=name)


class ProductMediaTestCase(TestCase):
    """Shared fixture: category plus an isolated ``MEDIA_ROOT`` per test."""

    def setUp(self):
        self.media_root = tempfile.mkdtemp(prefix='product-media-test-')
        self.addCleanup(shutil.rmtree, self.media_root, True)
        # Keep uploads out of the bind-mounted /app/media tree.
        self.media_override = override_settings(MEDIA_ROOT=self.media_root)
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)

        self.category = Category.objects.create(name='Home & Living', slug='home-living')
        cache.clear()

    def make_product(self, name='Ceramic Pour-Over Set', slug='ceramic-pour-over-set', image=True):
        product = Product.objects.create(
            category=self.category,
            name=name,
            slug=slug,
            price=Decimal('42.00'),
        )
        if image:
            content = build_test_jpeg()
            product.image.save(content.name, content, save=True)
        return product

    def page_html(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, f'GET {url} returned {response.status_code}')
        return response.content.decode()


class ProductImageUrlTests(ProductMediaTestCase):
    def test_url_prefixes_are_declared_absolute(self):
        # The declared settings values (not django.conf's script-prefixed view):
        # both prefixes must be site-root absolute, per Django's convention.
        from ecommerce import settings as settings_module

        self.assertTrue(
            settings_module.MEDIA_URL.startswith('/'),
            f"MEDIA_URL must be site-root absolute, got {settings_module.MEDIA_URL!r}",
        )
        self.assertTrue(
            settings_module.STATIC_URL.startswith('/'),
            f"STATIC_URL must be site-root absolute, got {settings_module.STATIC_URL!r}",
        )

    def test_image_url_is_site_root_absolute(self):
        product = self.make_product()
        self.assertTrue(
            product.image.url.startswith('/media/'),
            f'expected an absolute /media/ URL, got {product.image.url!r}',
        )

    def test_list_and_detail_pages_embed_the_same_absolute_url(self):
        product = self.make_product()
        image_url = product.image.url

        # The nested-path case: /<id>/<slug>/ (shop:product_detail). A URL that
        # rendered relative would resolve to /<id>/<slug>/media/products/... and
        # 404; both pages must embed the same site-root absolute URL.
        self.assertTrue(product.get_absolute_url().count('/') >= 3)
        list_html = self.page_html(reverse('shop:product_list'))
        detail_html = self.page_html(product.get_absolute_url())

        for page, html in (('list', list_html), ('detail', detail_html)):
            self.assertIn(f'src="{image_url}"', html, f'{page} page does not embed {image_url}')
            # Guard against a browser-relative media URL sneaking back in.
            self.assertNotIn('src="media/', html, f'{page} page renders a relative media URL')

    def test_upload_roundtrip_writes_under_products(self):
        product = self.make_product()

        # ImageField stores the relative name under upload_to='products/%Y/%m/%d'.
        self.assertTrue(
            product.image.name.startswith('products/'),
            f'unexpected upload path {product.image.name!r}',
        )
        self.assertTrue(product.image.storage.exists(product.image.name))
        self.assertTrue(product.image.path.startswith(self.media_root))
        self.assertTrue(product.image.url.startswith('/media/products/'))


class ProductImageFallbackTests(ProductMediaTestCase):
    def test_blank_image_falls_back_to_static_placeholder(self):
        product = self.make_product(image=False)
        self.assertFalse(product.image)

        for url in (reverse('shop:product_list'), product.get_absolute_url()):
            html = self.page_html(url)
            self.assertIn('/static/img/no_image.svg', html, f'{url} misses the placeholder')
            self.assertNotIn('src="static/', html, f'{url} renders a relative static URL')


class ProductAdminImageFieldTests(ProductMediaTestCase):
    def test_admin_change_form_exposes_file_input(self):
        product = self.make_product()
        staff = User.objects.create_superuser('media-admin', 'media@example.com', 'password')
        self.client.force_login(staff)

        response = self.client.get(reverse('admin:shop_product_change', args=[product.id]))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('type="file"', html)
        self.assertIn('name="image"', html)


class ProductMediaServingTests(ProductMediaTestCase):
    def test_debug_media_route_serves_uploaded_jpeg(self):
        product = self.make_product()
        media_url = f'/media/{product.image.name}'

        # ``ecommerce/urls.py`` appends the media route only while DEBUG is on,
        # and the test runner forces DEBUG off before any URLconf is imported,
        # so the route has to be (re)built here. Re-importing the real project
        # URLconf keeps this on the shipped wiring instead of a copy of it.
        self.addCleanup(self._restore_project_urls)
        with override_settings(DEBUG=True):
            importlib.reload(self.project_urls())
            clear_url_caches()
            response = self.client.get(media_url)

        self.assertEqual(response.status_code, 200)
        if response.streaming:
            body = b''.join(response.streaming_content)
        else:
            body = response.content
        self.assertEqual(body[:2], JPEG_MAGIC, 'served bytes are not a JPEG')

    @staticmethod
    def project_urls():
        import ecommerce.urls
        return ecommerce.urls

    @classmethod
    def _restore_project_urls(cls):
        """Re-import with the runner's DEBUG=False so later tests see the normal URLconf."""
        importlib.reload(cls.project_urls())
        clear_url_caches()
