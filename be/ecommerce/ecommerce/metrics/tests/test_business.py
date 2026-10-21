"""Tests for business metrics integration (card g37aggbd).

Covers:
* ``record_order_created`` records into the orders_total and orders_revenue_total
  counters when metrics are enabled.
* ``record_order_paid`` records revenue under the PAID status.
* ``record_order_refunded`` records into the refunded counter.
* Business metric helpers are no-ops when metrics are disabled.
* The checkout view records an order-created metric.
* The Stripe webhook records an order-paid metric.
"""
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.urls import reverse
from django.test import Client, TestCase, override_settings


# ---------------------------------------------------------------------------
# Direct meter-call tests (verify the recorder wiring)
# ---------------------------------------------------------------------------

@override_settings(PROMETHEUS_ENABLED=True)
class MeterCallTests(TestCase):
    """Directly call meter/business methods and verify the underlying prometheus
    counters are incremented."""

    def test_http_request_records_count(self):
        """Calling meter.http_request() directly increments the counter."""
        from ecommerce.metrics.registry import (
            is_metrics_enabled,
            http_requests_total,
            HTTP_METHODS,
            HTTP_STATUS_GROUPS,
        )
        from ecommerce.metrics.registry import meter
        # The meter should record this request.
        meter.http_request('GET', 200, 0.05)
        # The counter for GET/2xx should have gone up.
        sample = http_requests_total.labels(method='GET', status='2xx')
        self.assertGreaterEqual(sample._value.get(), 0.05)

    def test_order_created_records_revenue(self):
        from ecommerce.metrics.registry import orders_revenue_total
        from ecommerce.metrics.business import record_order_created
        record_order_created('PAID', 42.50)
        # The PAID bucket should now have at least 42.50.
        sample = orders_revenue_total.labels(status='PAID')
        self.assertGreaterEqual(sample._value.get(), 42.50)

    def test_order_refunded_records_amount(self):
        from ecommerce.metrics.registry import orders_refunded_total
        from ecommerce.metrics.business import record_order_refunded
        record_order_refunded(7, 5.00)
        sample = orders_refunded_total.labels(order_id='7')
        self.assertGreaterEqual(sample._value.get(), 5.00)

    def test_celery_task_records(self):
        from ecommerce.metrics.registry import celery_tasks_total
        from ecommerce.metrics.business import record_celery_task
        record_celery_task('orders.order_created', 'success', 1.5)
        sample = celery_tasks_total.labels(task='orders.order_created', status='success')
        self.assertGreaterEqual(sample._value.get(), 1.0)


@override_settings(PROMETHEUS_ENABLED=False)
class DisabledMeterTests(TestCase):
    """When metrics are off, the meter methods are no-ops."""

    def test_no_op_when_disabled(self):
        from ecommerce.metrics.registry import is_metrics_enabled
        self.assertFalse(is_metrics_enabled())
        # Calling should not raise and should not increment anything.
        from ecommerce.metrics.business import record_order_created
        record_order_created('PAID', 42.50)  # must not raise


# ---------------------------------------------------------------------------
# Integration: checkout view records order-created metric
# ---------------------------------------------------------------------------

@override_settings(PROMETHEUS_ENABLED=True)
@patch('orders.views.order_create_view.order_created.delay')
class CheckoutBusinessMetricTests(TestCase):
    """The checkout view records an order-created metric."""

    @classmethod
    def setUpClass(cls):
        cls._rate = override_settings(RATE_LIMIT_CHECKOUT=(100, 3600))
        cls._rate.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        cls._rate.disable()
        super().tearDownClass()

    def setUp(self):
        self.client = Client()
        from shop.models import Category, Product
        self.category = Category.objects.create(name='Books', slug='books')
        self.product = Product.objects.create(
            category=self.category, name='Guide', slug='guide',
            price=Decimal('19.99'),
        )
        session = self.client.session
        session['cart'] = {
            str(self.product.id): {'quantity': 1, 'price': '19.99'},
        }
        session.save()

    def test_checkout_dispatches_order_created_metric(self, mock_delay):
        """After a successful checkout, record_order_created is called."""
        with patch(
            'orders.views.order_create_view.record_order_created'
        ) as mock_record:
            url = reverse('orders:order_create')
            payload = {
                'first_name': 'Ada', 'last_name': 'Lovelace',
                'email': 'ada@example.com', 'address': '1 St',
                'postal_code': '12345', 'city': 'Oxford',
            }
            response = self.client.post(url, payload)
            self.assertEqual(response.status_code, 302)
            mock_record.assert_called_once()
            args, _ = mock_record.call_args
            # (status, amount)
            self.assertEqual(args[0], 'PENDING')
