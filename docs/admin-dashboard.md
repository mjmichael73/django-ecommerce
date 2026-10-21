# Admin Dashboard

A staff-only, read-only dashboard that gives an at-a-glance view of store
health: orders, revenue, order status, stock, returns and fraud signals. It is
intended for analysts and ops staff — not customers.

- **URL name:** `orders:admin_dashboard` (mounted at `/orders/admin/dashboard/`)
- **Protected by:** `@staff_member_required` — anonymous visitors are redirected
  to the admin login page; authenticated non-staff users are redirected too.
- **No model changes, no new dependencies.** Consumes the existing `Order`,
  `OrderItem`, `ReturnRequest`, `Product` and `auth.User` models only.

## What it shows

All metrics are computed with Django ORM aggregations in a single view
pass — no raw SQL, no per-object N+1.

| Block | What it shows | Window |
|-------|---------------|--------|
| Orders & revenue | Count of paid orders and revenue (sum of each paid order's `get_total_cost()`, i.e. discounted totals) | Today (local midnight onward) and rolling 7 days |
| Order status tally | Count of orders grouped by `Order.Status` | Last 7 days |
| Low stock | Tracked products at or below `LOW_STOCK_THRESHOLD = 5`, ascending, latest 10 | Always |
| Pending return requests | Count plus the latest 10 `SUBMITTED` returns | Always |
| Flagged fraud orders | Count plus the latest 10 orders with `is_flagged=True`, each linking to the order detail page (`orders:admin_order_detail`) | Always |
| Recent signups | Latest 10 users by `-date_joined` | Always |

## Metric definitions

### Orders & revenue

A "paid order" is `Order.objects.filter(paid=True)`. Revenue is the sum of each
order's discounted total — semantically `get_total_cost()` (gross line items
minus the order's percentage `discount`). The dashboard aggregates the gross
line-item sum (`Sum(F('items__price') * F('items__quantity'))`) per order in a
single grouped query, then applies each order's percentage discount in Python,
so a hundred paid orders do not cost a hundred queries.

> **Export-order disclaimer:** refunded amounts are **not** subtracted on this
> dashboard. Revenue is *gross paid* revenue. If you need net-of-refunds
> figures, use the order detail page or the CSV export in the Django admin,
> where `refunded_amount` is recorded per order.

### Low stock

`LOW_STOCK_THRESHOLD = 5` (module constant in
`orders/views/admin_dashboard_view.py`). Only products whose `stock` is tracked
(i.e. `stock__isnull=False`) and at or below the threshold are listed, sorted
ascending so `stock=0` (sold out) appears first.

Products with `stock=NULL` are **untracked** — by the `Product.stock` contract
(`NULL = untracked (always sellable)`) they are always sellable and are
intentionally **never** shown on the low-stock list.

### Pending returns

A return request counts as "pending" only when its status is
`ReturnRequest.Status.SUBMITTED`. Approved, refunded, rejected and canceled
requests do not count here.

### Flagged fraud

Orders with `is_flagged=True`. Fraud flags are set at Stripe webhook payment
completion by `orders/fraud.py` and only affect order rows — they never block a
payment. Each flagged order links to its detail page
(`orders:admin_order_detail`).

### Recent signups

Uses `django.contrib.auth.get_user_model()` (the default `auth.User`), ordered
by `-date_joined`.

## Styling & CSP

The template extends `admin/base_site.html` and uses the Django admin's own CSS
classes (`module`, `num`, `quiet`, etc.). Content Security Policy is enforced
app-wide by `ecommerce/csp_middleware.py` — the dashboard uses **no inline
`<script>`** and **no inline event handlers**; styling relies on the admin's
existing stylesheet only.
