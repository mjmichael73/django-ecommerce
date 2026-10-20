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
- [ ] Test data fixtures created
