# Testing — Django E-commerce

## Overview

Comprehensive testing strategy for Django e-commerce application with unit tests, integration tests, and end-to-end tests.

## Test Strategy

| Test Type | Tools | Coverage Target |
|-----------|-------|-----------------|
| **Unit Tests** | pytest-django | 90% models |
| **Integration Tests** | pytest + fixtures | 85% views |
| **E2E Tests** | Playwright | 80% user flows |
| **Performance Tests** | Locust | Load baselines |

## Setup

```bash
# Install test dependencies
pip install pytest pytest-django pytest-cov pytest-asyncio

# Run tests
pytest

# Run with coverage
pytest --cov=catalog --cov=cart --cov=orders --cov=payment --cov=coupons
```

## Unit Tests

### Model Tests

```python
# catalog/tests/test_models.py
import pytest
from catalog.models import Product, Category

@pytest.mark.django_db
class TestProduct:
    def test_product_creation(self):
        category = Category.objects.create(name="Electronics")
        product = Product.objects.create(
            name="Laptop",
            slug="laptop",
            price=999.99,
            category=category
        )
        assert product.name == "Laptop"
        assert product.price == 999.99
    
    def test_product_str(self):
        product = Product(name="Laptop")
        assert str(product) == "Laptop"
    
    def test_product_available(self):
        product = Product(available=True)
        assert product.is_available() is True
```

### Cart Tests

```python
# cart/tests/test_models.py
import pytest
from cart.cart import Cart
from catalog.models import Product

@pytest.mark.django_db
class TestCart:
    def test_add_to_cart(self, client):
        cart = Cart(client.session)
        product = Product.objects.create(
            name="Laptop",
            slug="laptop",
            price=999.99
        )
        cart.add(product, quantity=1)
        assert len(cart) == 1
    
    def test_remove_from_cart(self, client):
        cart = Cart(client.session)
        product = Product.objects.create(
            name="Laptop",
            slug="laptop",
            price=999.99
        )
        cart.add(product, quantity=1)
        cart.remove(product)
        assert len(cart) == 0
    
    def test_cart_total(self, client):
        cart = Cart(client.session)
        product1 = Product.objects.create(name="P1", slug="p1", price=100)
        product2 = Product.objects.create(name="P2", slug="p2", price=200)
        cart.add(product1, quantity=2)
        cart.add(product2, quantity=1)
        assert cart.get_total_price() == 400
```

## API Tests

### Checkout Tests

```python
# orders/tests/test_checkout.py
import pytest
from django.urls import reverse
from rest_framework.test import APIClient
from catalog.models import Product

@pytest.mark.django_db
class TestCheckout:
    def setup_method(self):
        self.client = APIClient()
        self.product = Product.objects.create(
            name="Laptop",
            slug="laptop",
            price=999.99,
            stock=10
        )
    
    def test_checkout_success(self):
        # Add to cart
        self.client.post(reverse('cart:add'), {
            'product_id': self.product.id,
            'quantity': 1
        })
        
        # Checkout
        response = self.client.post(reverse('orders:checkout'), {
            'shipping_address': '123 Test St'
        })
        
        assert response.status_code == 201
        assert response.data['status'] == 'pending'
    
    def test_checkout_empty_cart(self):
        response = self.client.post(reverse('orders:checkout'), {
            'shipping_address': '123 Test St'
        })
        
        assert response.status_code == 400
```

### Payment Tests

```python
# payment/tests/test_stripe.py
import pytest
from unittest.mock import patch, MagicMock
from payment.services import StripeService

class TestStripeService:
    @patch('stripe.checkout.Session.create')
    def test_create_checkout_session(self, mock_create):
        mock_create.return_value = MagicMock(id='cs_test_123')
        
        service = StripeService()
        session = service.create_checkout_session(
            order_id=1,
            amount=99999,
            currency='usd'
        )
        
        assert session.id == 'cs_test_123'
        mock_create.assert_called_once()
    
    @patch('stripe.Webhook.construct_event')
    def test_webhook_signature_verification(self, mock_verify):
        mock_verify.return_value = {'type': 'checkout.session.completed'}
        
        service = StripeService()
        event = service.verify_webhook(
            payload=b'test',
            sig_header='test_sig',
            secret='webhook_secret'
        )
        
        assert event['type'] == 'checkout.session.completed'
```

### Fraud Tests (card 832hc4eg)

```python
# orders/tests/test_fraud.py — flag-only fraud detection
# Per-rule tests via override_settings (small thresholds/windows, no sleeps:
# backdate through orders/*/update(created=...)); score capping at 100;
# velocity self-exclusion (at-threshold no flag, one more sibling flags);
# webhook integration (flags persisted exactly once, same replay is a
# no-op); evaluation raising must not un-pay, 500, or drop the invoice
# dispatch; mark_rules_reviewed admin action; and the GUARD that a flagged
# (fast-velocity) order still gets a 200 — flag never blocks.
docker compose run --rm -e SKIP_COLLECTSTATIC=1 -e REDIS_CACHE_URL= \
    -v "$PWD/be/ecommerce:/app" web python manage.py test \
    orders.tests.test_fraud payment.tests.test_stripe_webhook -v2
```

### Refund Tests (card a3cg42c4)

```python
# payment/tests/test_refunds.py — analyst-initiated refund primitives
# Guards (unpaid / no payment_intent / non-positive amount / already
# refunded locally) refuse *before* Stripe is called; full refund (amount
# omitted) and partial refund (cents conversion, accumulating); Stripe
# errors (generic, CardError, unexpected) returned as (False, reason) and
# never raised; the `issue_full_refund` admin action (success message,
# failure reason, double-click safety); and the inbound `charge.refunded`
# webhook branch (unknown payment_intent is a 200 no-op, several refunds
# keep refund_id blank, checkout.session.completed still unaffected).
# stripe.Refund.create is mocked everywhere — the suite never calls Stripe.
docker compose run --rm -e SKIP_COLLECTSTATIC=1 -e REDIS_CACHE_URL= \
    -v "$PWD/be/ecommerce:/app" web python manage.py test \
    payment.tests.test_refunds payment.tests.test_stripe_webhook -v2
```

### Product media tests (card ga4e3hg5)

```python
# shop/tests/test_product_media.py — product image upload + media serving
# Rendered image URLs are site-root absolute (`/media/...`) on both the list
# page and the nested detail page `/<id>/<slug>/`; the declared
# MEDIA_URL/STATIC_URL settings values carry a leading slash; an uploaded
# Pillow-generated ContentFile round-trips under `products/`; a blank image
# falls back to `/static/img/no_image.svg`; the admin change form exposes the
# file input; and the DEBUG-only `/media/<path>` route (rebuilt via
# override_settings(DEBUG=True), because the test runner forces DEBUG=False)
# serves the JPEG bytes back. Uploads are written to a per-test temp
# MEDIA_ROOT, never into the bind-mounted /app/media tree.
docker compose run --rm -e SKIP_COLLECTSTATIC=1 -e REDIS_CACHE_URL= \
    -v "$PWD/be/ecommerce:/app" web python manage.py test shop.tests -v2
```

### Product variant tests (card g99ha567)

```python
# shop/tests/test_product_variants.py — one-dimensional product variants
# Model: unit_price() override vs. product-price fallback, __str__, the
# partial per-product SKU constraint (many blanks allowed, duplicates not).
# Form: the optional variant selector only exists when a variants queryset is
# passed and renders as a <select> on the detail page; a plain product keeps
# the historical quantity/override-only form. Cart: plain entries keep their
# exact two-key shape, variant lines are separate '<pid>:<vid>' entries with
# the variant price snapshot, two variants are two lines, remove/override
# target one line, iteration attaches product + variant, totals sum lines;
# cross-product, unavailable and non-numeric variant ids degrade to a plain
# line (no 500). Orders: checkout stores the variant and effective price, a
# variant-less order stores NULL, and deleting the variant keeps the order
# line (SET_NULL + price snapshot). Stripe: the mocked Session.create line
# item name becomes '<product> — <variant>' and plain names are unchanged.
# Admin: the product change form carries the variant tabular inline.
docker compose run --rm -e SKIP_COLLECTSTATIC=1 -e REDIS_CACHE_URL= \
    -v "$PWD/be/ecommerce:/app" web python manage.py test \
    shop.tests.test_product_variants -v2
```

### Inventory tracking tests (card 44ge8hgb)

```python
# shop/tests/test_inventory.py — nullable stock (NULL = untracked = always
# sellable) on Product and ProductVariant; decrement once per paid order inside
# the webhook's guarded `updated == 1` branch, clamped at 0, never negative.
# Covers: untracked no-op + is_in_stock(); product vs variant decrement in one
# order; clamp-to-zero with assertLogs warning; webhook integration (decrement
# exactly once, replay is a no-op, flagged orders still decrement); detail-page
# "In stock"/"Sold out" badge with no numeric count for untracked; admin
# changelist columns for stock + is_in_stock.
docker compose run --rm -e SKIP_COLLECTSTATIC=1 -e REDIS_CACHE_URL= \
    -v "$PWD/be/ecommerce:/app" web python manage.py test \
    shop.tests.test_inventory -v2
```

### Product reviews tests (card bd8f759h)

```python
# shop/tests/test_product_reviews.py — one review per user per product
# (DB unique constraint), user FK on to PROTECT; rating validated 1-5 at
# the model; POST-only redirect view with success / already-reviewed /
# invalid-input messages (no dedicated template, the detail page is the
# review surface); detail-page summary chip + list + form vs. log-in link
# (reviewer sees own review, no resubmission form); `review` rate-limit
# scope (429 at the window edge, GETs never consume quota); admin
# approve/unapprove actions flip rows and message the admin.
docker compose run --rm -e SKIP_COLLECTSTATIC=1 -e REDIS_CACHE_URL= \
    -v "$PWD/be/ecommerce:/app" web python manage.py test \
    shop.tests.test_product_reviews -v2
```


### Integration tests (card e574da32)

```python
# orders/tests/test_integration.py — end-to-end cart -> orders -> payment -> coupons
# Drives the real view layer (cart_add, coupon_apply, order_create, payment_process,
# stripe_webhook) via HTTP POST/GET across the full purchase flow. Stripe Checkout
# Session.create and the Celery order_created / payment_completed tasks are mocked
# exactly as the per-app unit tests do. Rate-limit ceilings are overridden to a
# per-process LocMem ceiling so multi-POST flows never hit 429. Covers: guest
# checkout (order created, cart cleared, task enqueued); Stripe session creation
# with idempotency_key + line items; webhook marks paid + single invoice
# dispatch + idempotent replay; authenticated checkout sets order.user FK;
# variant line-item price snapshot + Stripe line-item name 'Product — Variant';
# mixed plain + variant cart; coupon apply (valid / invalid / expired); coupon
# discount math on Cart; authenticated purchase -> order history visibility;
# payment_process short-circuit on paid orders; unpaid order creates Stripe
# session with unit_amount from stored OrderItem.price.
#
# Note: order_create does NOT persist the coupon FK / discount onto the Order row
# — the discount is only reflected in the cart's get_total_price_after_discount.
# These tests pin that observed behaviour.
docker compose run --rm -e SKIP_COLLECTSTATIC=1 -e REDIS_CACHE_URL= \
    -v "$PWD/be/ecommerce:/app" -v media_data:/app/media web python manage.py test \
    orders.tests.test_integration -v2
```

### Full-text search tests (card bf247ef9)

```python
# shop/tests/test_full_text_search.py - catalog ?q= full-text search
# Postgres-native on-the-fly SearchVector (name weight A > description weight B config 'english') with a plain SearchQuery, ranked ordering (rank desc, name asc); available=True and category filters compose; empty/whitespace/missing q = plain listing; friendly empty state; query echo and product names autoescaped. CI runs SQLite, where the same call degrades to an icontains OR - stemming/ranking tests skip there (PostgresOnlyTests).
docker compose run --rm -e SKIP_COLLECTSTATIC=1 -e REDIS_CACHE_URL= -v "$PWD/be/ecommerce:/app" web python manage.py test shop.tests.test_full_text_search -v2
```

### Performance tests (card e4e4dh4g)

```python
# ecommerce/tests/test_performance.py — SQL query-budget and relative
# wall-clock regression tests for the storefront's hottest paths.
# Design: no brittle timing assertions — every test guards a *structural*
# invariant (query-count ceiling via CaptureQueriesContext, result sizes,
# relative scaling, repeated-checkout stability). Rate limits are overridden
# to (0, 0) per test class to disable the limiter entirely so burst
# checkout POSTs don't hit 429.
# The Celery order_created.delay task is mocked out. Stripe is never
# exercised here (that lives in payment:process). Runs on the LocMem cache
# (see test_runner.py) — never touches shared Redis DB 2.
docker compose run --rm -e SKIP_COLLECTSTATIC=1 -e REDIS_CACHE_URL= -v "$PWD/be/ecommerce:/app" web python manage.py test ecommerce.tests.test_performance -v2
```

## Integration Tests

### Full Flow Tests

```python
# tests/integration/test_purchase_flow.py
import pytest
from django.test import TestCase
from catalog.models import Product, Category
from orders.models import Order

@pytest.mark.django_db
class TestPurchaseFlow(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name="Electronics")
        self.product = Product.objects.create(
            name="Laptop",
            slug="laptop",
            price=999.99,
            category=self.category,
            stock=10
        )
    
    def test_complete_purchase_flow(self):
        # 1. Browse catalog
        response = self.client.get('/catalog/')
        self.assertEqual(response.status_code, 200)
        
        # 2. View product
        response = self.client.get(f'/catalog/{self.product.slug}/')
        self.assertEqual(response.status_code, 200)
        
        # 3. Add to cart
        response = self.client.post('/cart/add/', {
            'product_id': self.product.id,
            'quantity': 1
        })
        self.assertEqual(response.status_code, 200)
        
        # 4. Checkout
        response = self.client.post('/checkout/', {
            'shipping_address': '123 Test St',
            'payment_method': 'card'
        })
        self.assertEqual(response.status_code, 200)
        
        # 5. Verify order created
        order = Order.objects.latest('id')
        self.assertEqual(order.total, 999.99)
```

## E2E Tests

```python
# tests/e2e/test_checkout.py
from playwright.sync_api import sync_playwright

def test_checkout_flow():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        
        # Browse catalog
        page.goto('http://localhost:8089/catalog/')
        page.click('.product-card')
        
        # Add to cart
        page.click('button:has-text("Add to Cart")')
        
        # Go to cart
        page.goto('http://localhost:8089/cart/')
        assert page.locator('.cart-item').count() == 1
        
        # Checkout
        page.click('button:has-text("Checkout")')
        page.fill('#shipping-address', '123 Test St')
        page.click('button:has-text("Place Order")')
        
        # Verify success
        assert 'Order Confirmed' in page.content()
        
        browser.close()
```

## Test Commands

```bash
# Run all tests
make test

# Run the email-verification test module (card d7ac2957)
docker compose run --rm -e SKIP_COLLECTSTATIC=1 -e REDIS_CACHE_URL= -v "$PWD/be/ecommerce:/app" web python manage.py test accounts.tests.test_email_verification -v2

# Run the password-reset test module (card 9ee2accb)
docker compose run --rm -e SKIP_COLLECTSTATIC=1 -e REDIS_CACHE_URL= -v "$PWD/be/ecommerce:/app" web python manage.py test accounts.tests.test_password_reset -v2

# Run the payment-idempotency test module (card 7e732hbh)
docker compose run --rm -e SKIP_COLLECTSTATIC=1 -e REDIS_CACHE_URL= -v "$PWD/be/ecommerce:/app" web python manage.py test payment.tests.test_payment_idempotency -v2

# Run the fraud-detection test module (card 832hc4eg)
docker compose run --rm -e SKIP_COLLECTSTATIC=1 -e REDIS_CACHE_URL= -v "$PWD/be/ecommerce:/app" web python manage.py test orders.tests.test_fraud payment.tests.test_stripe_webhook -v2

# Run the refund-handling test module (card a3cg42c4)
docker compose run --rm -e SKIP_COLLECTSTATIC=1 -e REDIS_CACHE_URL= -v "$PWD/be/ecommerce:/app" web python manage.py test payment.tests.test_refunds payment.tests.test_stripe_webhook -v2

# Run the product media/static URL test module (card ga4e3hg5)
docker compose run --rm -e SKIP_COLLECTSTATIC=1 -e REDIS_CACHE_URL= -v "$PWD/be/ecommerce:/app" web python manage.py test shop.tests -v2

# Run unit tests
make test-unit

# Run integration tests
make test-integration

# Run E2E tests
make test-e2e

# Run with coverage
make test-coverage

# Run specific test
pytest catalog/tests/test_models.py::TestProduct::test_product_creation
```

> **Note:** the suite always runs on a per-process LocMem cache, never on the
> shared Redis (DB 2) that holds the POST rate-limit counters — those survive
> between runs and would make the suite depend on how often it was started.
> `make test` therefore also passes an empty `REDIS_CACHE_URL`.

## Validation Checklist

- [ ] Model tests for all apps
- [ ] View tests for all endpoints
- [ ] API tests for checkout flow
- [ ] Payment integration tests
- [ ] E2E tests for user journeys
- [ ] Test coverage > 80%
- [ ] CI/CD pipeline configured
- [x] Email verification test module `accounts.tests.test_email_verification` (card d7ac2957)
- [x] Fraud detection test module `orders.tests.test_fraud` (card 832hc4eg)
- [x] Refund handling test module `payment.tests.test_refunds` (card a3cg42c4)
- [x] Product media test module `shop.tests.test_product_media` (card ga4e3hg5)
- [x] Product variant test module `shop.tests.test_product_variants` (card g99ha567)
- [x] Inventory tracking test module `shop.tests.test_inventory` (card 44ge8hgb)
- [x] Product reviews test module `shop.tests.test_product_reviews` (card bd8f759h)
- [x] Full-text search test module `shop.tests.test_full_text_search` (card bf247ef9)
- [x] Faceted navigation test module `shop.tests.test_facets` (card 3g83heaa)
- [x] Performance test module `ecommerce.tests.test_performance` (card e4e4dh4g) — query-budget and relative-slowdown regression tests for catalog listing, product detail, cart detail, and checkout POST endpoints
- [x] Wishlist test module `wishlist.tests` (card cc26292g)
- [ ] Test data fixtures created
