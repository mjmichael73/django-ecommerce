"""Staff-only admin dashboard (card d3gh87d2).

Aggregates the key store metrics a staff member needs at a glance — orders,
revenue, order status, low stock, pending returns, flagged fraud orders and
recent signups — in a single aggregate pass over existing models. No model
changes, no raw SQL, no extra dependencies.
"""
from datetime import timedelta

from decimal import Decimal

from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import get_user_model
from django.db.models import F, IntegerField, Sum
from django.db.models.functions import Coalesce
from django.shortcuts import render
from django.utils import timezone

from orders.models import Order
from returns.models import ReturnRequest
from shop.models import Product

User = get_user_model()

# Tracked products at or below this stock level are surfaced as "low stock".
# ``stock`` is NULL for untracked products — those are never shown here (they
# are always sellable per the Product docstring).
LOW_STOCK_THRESHOLD = 5


def _grouped_revenue(qs):
    """Discounted revenue for a queryset of paid orders, in one grouped query.

    Revenue is the sum of per-order ``get_total_cost()`` semantics: the
    discounted totals of paid orders. We aggregate the gross line-item sum with
    a DB aggregation (``Sum(F('items__price') * F('items__quantity'))``) grouped
    per order, then fold in each order's percentage ``discount`` with Python
    math — so there is no per-order N+1, only one grouped query per call.

    Export-order disclaimer (card d3gh87d2): refunded amounts are NOT
    subtracted on this dashboard. Revenue is gross paid revenue.
    """
    rows = qs.values('id', 'discount').annotate(
        gross=Coalesce(
            Sum(F('items__price') * F('items__quantity')),
            0,
            output_field=IntegerField(),
        ),
    )
    total = Decimal(0)
    for row in rows:
        gross = Decimal(row['gross'] or 0)
        discount_pct = row['discount'] or 0
        total += gross * (Decimal(100) - Decimal(discount_pct)) / Decimal(100)
    return total


@staff_member_required
def admin_dashboard(request):
    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=7)

    paid_today = Order.objects.filter(
        paid=True, created__gte=today_start,
    )
    paid_week = Order.objects.filter(
        paid=True, created__gte=week_start,
    )

    # --- Orders + revenue ------------------------------------------------
    orders_today_count = paid_today.count()
    revenue_today = _grouped_revenue(paid_today)
    orders_week_count = paid_week.count()
    revenue_week = _grouped_revenue(paid_week)

    # --- Order status tally (last 7 days) --------------------------------
    status_counts = {
        item['status']: item['count']
        for item in Order.objects.filter(
            created__gte=week_start,
        ).values('status').annotate(
            count=Coalesce(Sum(1), 0, output_field=IntegerField()),
        )
    }
    # Ensure every defined status appears (with zero) for stable rendering.
    for status_value, _ in Order.Status.choices:
        status_counts.setdefault(status_value, 0)
    status_tally = [
        {'status': sv, 'label': sl, 'count': status_counts[sv]}
        for sv, sl in Order.Status.choices
    ]

    # --- Low stock --------------------------------------------------------
    # Tracked stock only (stock__isnull=False), at or below the threshold,
    # ascending. Includes stock=0 (sold out). Untracked (NULL) products are
    # never listed.
    low_stock = (
        Product.objects
        .filter(stock__isnull=False, stock__lte=LOW_STOCK_THRESHOLD)
        .only('name', 'stock')
        .order_by('stock')[:10]
    )

    # --- Pending returns --------------------------------------------------
    pending_returns_count = ReturnRequest.objects.filter(
        status=ReturnRequest.Status.SUBMITTED,
    ).count()
    pending_returns = (
        ReturnRequest.objects
        .select_related('order')
        .only('id', 'order__id', 'created')
        .filter(status=ReturnRequest.Status.SUBMITTED)
        .order_by('-created')[:10]
    )

    # --- Flagged fraud orders --------------------------------------------
    flagged_count = Order.objects.filter(is_flagged=True).count()
    flagged_orders = (
        Order.objects
        .filter(is_flagged=True)
        .only('id', 'fraud_score', 'fraud_notes', 'created')
        .order_by('-created')[:10]
    )

    # --- Recent signups ---------------------------------------------------
    recent_signups = (
        User.objects
        .only('username', 'email', 'date_joined')
        .order_by('-date_joined')[:10]
    )

    context = {
        # Orders + revenue
        'orders_today_count': orders_today_count,
        'revenue_today': revenue_today,
        'orders_week_count': orders_week_count,
        'revenue_week': revenue_week,
        # Order status tally
        'status_tally': status_tally,
        # Low stock
        'low_stock': low_stock,
        'low_stock_threshold': LOW_STOCK_THRESHOLD,
        # Pending returns
        'pending_returns_count': pending_returns_count,
        'pending_returns': pending_returns,
        # Flagged fraud
        'flagged_count': flagged_count,
        'flagged_orders': flagged_orders,
        # Recent signups
        'recent_signups': recent_signups,
    }
    return render(request, 'admin/orders/dashboard.html', context)
