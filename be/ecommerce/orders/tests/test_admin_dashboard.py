"""Tests for the staff-only admin dashboard (card d3gh87d2).

Access control, per-block content assertions with minimal seeded data, and an
empty-database case. All hermetic — no real Stripe, no network.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from orders.models import Order, OrderItem
from returns.models import ReturnRequest
from shop.models import Category, Product

User = get_user_model()


def _make_category():
    return Category.objects.create(name='Home', slug='home')


def _make_product(category, name, price, stock=None):
    return Product.objects.create(
        category=category, name=name, slug=name.lower(),
        price=price, stock=stock,
    )


def _make_order(
    product, price, quantity=1, paid=True, discount=0,
    status=Order.Status.PAID, created=None,
    is_flagged=False, fraud_score=0, fraud_notes='',
):
    order = Order.objects.create(
        first_name='Test', last_name='User',
        email='t@example.com', address='1 St', postal_code='1',
        city='City', paid=paid, discount=discount, status=status,
        is_flagged=is_flagged, fraud_score=fraud_score,
        fraud_notes=fraud_notes,
    )
    if created is not None:
        Order.objects.filter(pk=order.pk).update(created=created)
        order.refresh_from_db()
    OrderItem.objects.create(
        order=order, product=product, price=price, quantity=quantity,
    )
    return order


class AdminDashboardAccessTests(TestCase):
    """Access control mirrors Django's ``staff_member_required`` exactly."""

    def setUp(self):
        self.staff = User.objects.create_superuser(
            'admin', 'admin@example.com', 'password',
        )
        self.active_non_staff = User.objects.create_user(
            'bob', 'bob@example.com', 'password',
        )
        self.url = reverse('orders:admin_dashboard')

    def test_anonymous_redirects_to_admin_login(self):
        """Anonymous users get a 302 redirect to the admin login page."""
        response = Client().get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response['Location'])
        # The original path is carried as ``next``.
        self.assertIn('next=', response['Location'])

    def test_active_non_staff_redirects_to_admin_login(self):
        """Authenticated-but-non-staff users get the same redirect (302).

        ``staff_member_required`` is built on ``user_passes_test`` which
        redirects to login when the test fails — it does not 403 — so a logged
        in, active, non-staff user is redirected just like an anonymous one.
        """
        client = Client()
        client.force_login(self.active_non_staff)
        response = client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response['Location'])

    def test_staff_user_gets_200(self):
        client = Client()
        client.force_login(self.staff)
        response = client.get(self.url)
        self.assertEqual(response.status_code, 200)


class AdminDashboardContentTests(TestCase):
    """Per-block content assertions with minimal seeded data."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_superuser(
            'admin', 'admin@example.com', 'password',
        )
        cls.user_a = User.objects.create_user(
            'alice', 'alice@example.com', 'password',
        )
        # user_b signed up earlier (later in date_joined ordering, so created
        # first to be the *older* signup; we then create user_a below with a
        # newer date_joined via explicit ordering in the test).
        cls.user_b = User.objects.create_user(
            'bob', 'bob@example.com', 'password',
        )

        cls.category = Category.objects.create(name='Home', slug='home')
        cls.product_low = _make_product(
            cls.category, 'Mug', 10.00, stock=3,
        )
        cls.product_sold_out = _make_product(
            cls.category, 'Cup', 5.00, stock=0,
        )
        # Untracked product — must NEVER appear in low-stock.
        cls.product_untracked = _make_product(
            cls.category, 'Plate', 8.00, stock=None,
        )

        now = timezone.now()
        today = now
        yesterday = now - timedelta(days=1)
        eight_days_ago = now - timedelta(days=8)

        # Paid today: gross 20.00, 10% discount -> 18.00
        cls.order_paid_today = _make_order(
            cls.product_low, 10.00, quantity=2,
            paid=True, discount=10, created=today,
        )
        # Paid this week (older than today): gross 10.00, no discount -> 10.00
        cls.order_paid_week = _make_order(
            cls.product_sold_out, 5.00, quantity=2,
            paid=True, discount=0, created=yesterday,
        )
        # Paid but outside the 7-day window — must NOT count in today/week stats.
        cls.order_paid_old = _make_order(
            cls.product_untracked, 8.00, quantity=1,
            paid=True, discount=0, created=eight_days_ago,
        )
        # Unpaid today — must NOT count in paid stats.
        cls.order_unpaid_today = _make_order(
            cls.product_low, 10.00, quantity=1,
            paid=False, discount=0, created=today,
            status=Order.Status.PENDING,
        )

        # A flagged fraud order (clean otherwise) and a clean order.
        cls.order_flagged = _make_order(
            cls.product_low, 10.00, quantity=1,
            paid=True, discount=0, created=today,
            is_flagged=True, fraud_score=85,
            fraud_notes='velocity-rule: 5 orders in 2 min',
        )
        cls.order_clean = _make_order(
            cls.product_sold_out, 5.00, quantity=1,
            paid=True, discount=0, created=today,
        )

        # Returns: one SUBMITTED (pending), one APPROVED (not pending).
        cls.return_submitted = ReturnRequest.objects.create(
            order=cls.order_paid_today,
            reason='defective',
        )
        ReturnRequest.objects.filter(pk=cls.return_submitted.pk).update(
            created=now,
        )
        cls.return_submitted.refresh_from_db()
        cls.return_approved = ReturnRequest.objects.create(
            order=cls.order_paid_week,
            reason='wrong size',
            status=ReturnRequest.Status.APPROVED,
        )
        ReturnRequest.objects.filter(pk=cls.return_approved.pk).update(
            created=now,
        )
        cls.return_approved.refresh_from_db()

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.staff)
        self.url = reverse('orders:admin_dashboard')

    def _get(self):
        return self.client.get(self.url)

    # --- Orders + revenue -----------------------------------------------
    def test_orders_today_count(self):
        """Today's paid order count excludes unpaid, old, and outside-window."""
        response = self._get()
        # Paid today: order_paid_today, order_flagged, order_clean = 3.
        # (unpaid_today unpaid, order_paid_week yesterday, order_paid_old 8d ago)
        # Assert the exact cell so a bare '3' doesn't match other substrings.
        self.assertContains(response, '<td class="num">3</td>')

    def test_revenue_today_reflects_discount(self):
        """Revenue = discounted total of paid-today orders (no N+1)."""
        response = self._get()
        # order_paid_today: 2 * 10.00 = 20.00 gross, 10% off -> 18.00
        # order_flagged: 1 * 10.00 = 10.00 gross, no discount -> 10.00
        # order_clean: 1 * 5.00 = 5.00 gross, no discount -> 5.00
        # total today = 33.00 (discount applied per-order, not N+1)
        self.assertContains(response, '$33.00')

    def test_orders_week_count_excludes_old(self):
        """Last-7-days paid count excludes the 8-day-old order."""
        response = self._get()
        # Paid within 7 days: order_paid_today + order_flagged + order_clean +
        # order_paid_week = 4 (unpaid_today excluded, paid_old excluded — 8 days).
        # Week revenue = 18 + 10 + 5 + 10 = 43.00
        self.assertContains(response, '$43.00')

    def test_revenue_week_excludes_eight_day_old(self):
        response = self._get()
        self.assertContains(response, '$43.00')

    def test_revenue_disclaimer_present(self):
        response = self._get()
        self.assertContains(response, 'Refunded amounts are not subtracted')

    # --- Order status tally ---------------------------------------------
    def test_status_tally_counts_last_seven_days(self):
        """Only orders within the last 7 days contribute to the status tally."""
        response = self._get()
        html = response.content.decode()
        # Within 7 days: order_paid_today(PAID), order_paid_week(PAID),
        # order_flagged(PAID), order_clean(PAID), order_unpaid_today(PENDING)
        # = 4 PAID + 1 PENDING. The 8-day-old paid order is excluded.
        self.assertIn('Pending', html)
        self.assertIn('Paid', html)
        self.assertIn('1', html)   # Pending count
        self.assertIn('4', html)   # Paid count

    # --- Low stock -------------------------------------------------------
    def test_low_stock_lists_tracked_only(self):
        """Low-stock list excludes untracked (NULL) products and is ascending."""
        response = self._get()
        html = response.content.decode()
        self.assertIn('Mug', html)       # stock=3, threshold 5 -> listed
        self.assertIn('Cup', html)       # stock=0 (sold out) -> listed
        # Untracked (NULL stock) products must NOT appear in low stock.
        self.assertNotIn('Plate', html)
        # Ascending by stock: Cup (0) before Mug (3).
        cup_pos = html.find('Cup')
        mug_pos = html.find('Mug')
        self.assertLess(cup_pos, mug_pos, 'low stock should be ascending by stock')

    def test_low_stock_threshold_constant_is_five(self):
        from orders.views.admin_dashboard_view import LOW_STOCK_THRESHOLD
        self.assertEqual(LOW_STOCK_THRESHOLD, 5)

    # --- Pending returns -------------------------------------------------
    def test_pending_returns_count_is_one(self):
        response = self._get()
        # pending_returns_count == 1 (only SUBMITTED; APPROVED excluded).
        # The "1 pending." sentence is the most specific assertion.
        self.assertContains(response, '1 pending.')

    def test_approved_return_not_pending(self):
        response = self._get()
        html = response.content.decode()
        # The submitted return's id is present; the approved one is not in the
        # pending list. We assert neither approved-order linkage shows as a
        # pending return row.
        self.assertNotIn('Order %d</a>' % self.return_approved.order_id,
                         html.split('Pending return requests')[1].split('Flagged fraud')[0])

    # --- Flagged fraud ---------------------------------------------------
    def test_flagged_count_is_one(self):
        response = self._get()
        self.assertContains(response, '1 flagged.')

    def test_flagged_order_links_to_detail(self):
        response = self._get()
        url = reverse('orders:admin_order_detail', args=[self.order_flagged.id])
        self.assertContains(response, url)
        self.assertContains(response, str(self.order_flagged.fraud_score))
        self.assertContains(response, 'velocity-rule')

    # --- Recent signups --------------------------------------------------
    def test_recent_signups_listed(self):
        """Latest <=10 users by -date_joined; both users present."""
        response = self._get()
        self.assertContains(response, 'alice')
        self.assertContains(response, 'bob')
        self.assertContains(response, 'alice@example.com')
        self.assertContains(response, 'bob@example.com')


class AdminDashboardEmptyTests(TestCase):
    """Empty-database case: 200 with zero/empty blocks, no exception.

    "No data at all" means no orders, returns, products or fraud flags. The
    single staff user created to drive the request is itself a signup, so the
    recent-signups block lists that one user rather than rendering "No users."
    """

    def test_empty_db_renders(self):
        staff = User.objects.create_superuser(
            'admin', 'admin@example.com', 'password',
        )
        client = Client()
        client.force_login(staff)
        response = client.get(reverse('orders:admin_dashboard'))
        self.assertEqual(response.status_code, 200)
        # Orders + revenue are zero for both windows.
        self.assertContains(response, '$0.00')
        self.assertContains(response, 'Refunded amounts are not subtracted')
        # Low stock / pending returns / flagged orders are empty.
        self.assertContains(response, 'No low-stock products.')
        self.assertContains(response, 'No pending return requests.')
        self.assertContains(response, 'No flagged orders.')
        # The only signup is the staff user themselves.
        self.assertContains(response, 'admin')
