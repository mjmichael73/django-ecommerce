import hashlib
from decimal import Decimal
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify
from PIL import Image

from shop.models import Category, Product

User = get_user_model()

SUPERUSER_USERNAME = 'superadmin'
SUPERUSER_EMAIL = 'superadmin@example.com'
SUPERUSER_PASSWORD = 'password'

# Demo catalog: categories implied by first product in group
SEED_ROWS = [
    {
        'category': 'Home & Living',
        'category_slug': 'home-living',
        'name': 'Ceramic Pour-Over Set',
        'slug': 'ceramic-pour-over-set',
        'description': (
            'Stackable dripper and carafe in matte glaze. Fits standard filters; '
            'dishwasher-safe.'
        ),
        'price': '42.00',
    },
    {
        'category': 'Home & Living',
        'category_slug': 'home-living',
        'name': 'Linen Table Runner',
        'slug': 'linen-table-runner',
        'description': 'Pre-washed European linen. 220×45 cm. Natural oat tone.',
        'price': '58.50',
    },
    {
        'category': 'Electronics',
        'category_slug': 'electronics',
        'name': 'Noise-Cancelling Headphones',
        'slug': 'noise-cancelling-headphones',
        'description': (
            'Over-ear, 30h playback, USB-C fast charge. Comfortable memory-foam ear cups.'
        ),
        'price': '199.99',
    },
    {
        'category': 'Electronics',
        'category_slug': 'electronics',
        'name': 'Portable Bluetooth Speaker',
        'slug': 'portable-bluetooth-speaker',
        'description': 'IPX7 waterproof. 360° sound. Pairs two units for stereo.',
        'price': '79.00',
    },
    {
        'category': 'Accessories',
        'category_slug': 'accessories',
        'name': 'Vegetable-Tanned Tote',
        'slug': 'vegetable-tanned-tote',
        'description': 'Full-grain leather, inner zip pocket, fits a 13" laptop.',
        'price': '135.00',
    },
    {
        'category': 'Accessories',
        'category_slug': 'accessories',
        'name': 'Titanium Travel Mug',
        'slug': 'titanium-travel-mug',
        'description': 'Double-wall vacuum, 350 ml, leak-proof flip lid.',
        'price': '44.00',
    },
]


def _colors_from_seed(text: str):
    h = hashlib.sha256(text.encode()).hexdigest()
    return (
        tuple(int(h[i : i + 2], 16) for i in (0, 2, 4)),
        tuple(max(0, int(h[i : i + 2], 16) - 55) for i in (6, 8, 10)),
    )


def build_placeholder_jpeg(name: str) -> ContentFile:
    """Deterministic gradient JPEG (no network) for demo product photos."""
    w, h = 800, 800
    top, bottom = _colors_from_seed(name)
    img = Image.new('RGB', (w, h))
    px = img.load()
    for y in range(h):
        t = y / (h - 1) if h > 1 else 0
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        row = (max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))
        for x in range(w):
            px[x, y] = row
    buf = BytesIO()
    img.save(buf, format='JPEG', quality=90, optimize=True)
    buf.seek(0)
    filename = f'seed-{slugify(name)}.jpg'
    return ContentFile(buf.read(), name=filename)


class Command(BaseCommand):
    help = (
        'Seed categories, products (with generated placeholder images), and a superuser '
        f'(username {SUPERUSER_USERNAME!r}, password {SUPERUSER_PASSWORD!r}). '
        'Safe to run multiple times.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--no-images',
            action='store_true',
            help='Create/update products without touching images.',
        )
        parser.add_argument(
            '--no-superuser',
            action='store_true',
            help='Skip creating or updating the admin superuser.',
        )

    def handle(self, *args, **options):
        no_images = options['no_images']
        no_superuser = options['no_superuser']

        with transaction.atomic():
            categories_by_slug = {}
            for row in SEED_ROWS:
                cs = row['category_slug']
                if cs not in categories_by_slug:
                    cat, created = Category.objects.get_or_create(
                        slug=cs,
                        defaults={'name': row['category']},
                    )
                    if not created and cat.name != row['category']:
                        cat.name = row['category']
                        cat.save(update_fields=['name'])
                    categories_by_slug[cs] = cat
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"Category {'created' if created else 'exists'}: {cat.name}"
                        )
                    )

            for row in SEED_ROWS:
                cat = categories_by_slug[row['category_slug']]
                price = Decimal(row['price'])
                product, created = Product.objects.get_or_create(
                    category=cat,
                    slug=row['slug'],
                    defaults={
                        'name': row['name'],
                        'description': row['description'],
                        'price': price,
                        'available': True,
                    },
                )
                update_fields = []
                if product.name != row['name']:
                    product.name = row['name']
                    update_fields.append('name')
                if product.description != row['description']:
                    product.description = row['description']
                    update_fields.append('description')
                if product.price != price:
                    product.price = price
                    update_fields.append('price')
                if not product.available:
                    product.available = True
                    update_fields.append('available')
                if update_fields:
                    product.save(update_fields=update_fields)

                if not no_images:
                    img_file = build_placeholder_jpeg(row['name'])
                    product.image.save(img_file.name, img_file, save=True)

                self.stdout.write(
                    self.style.SUCCESS(
                        f"Product {'created' if created else 'updated'}: {product.name}"
                    )
                )

            if not no_superuser:
                user, created = User.objects.get_or_create(
                    username=SUPERUSER_USERNAME,
                    defaults={
                        'email': SUPERUSER_EMAIL,
                        'is_staff': True,
                        'is_superuser': True,
                    },
                )
                user.email = SUPERUSER_EMAIL
                user.is_staff = True
                user.is_superuser = True
                user.set_password(SUPERUSER_PASSWORD)
                user.save()
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Superuser {'created' if created else 'updated'}: "
                        f"{SUPERUSER_USERNAME} (password: {SUPERUSER_PASSWORD})"
                    )
                )

        self.stdout.write(self.style.SUCCESS('Seeding complete.'))
