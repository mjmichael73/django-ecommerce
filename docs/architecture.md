# Architecture — Django E-commerce

## Overview

A Django 5 demo shop with session-based cart, checkout, Stripe Checkout payments, coupon discounts, and Celery background jobs for order emails and PDF invoices (WeasyPrint).

**Stack:** Django 5.0, PostgreSQL, Redis, RabbitMQ, Celery 5, Stripe, WeasyPrint, Gunicorn, Docker Compose

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Nginx Reverse Proxy                            │
│                              (Port 80/443)                                  │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    │               │               │
                    ▼               ▼               ▼
┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐
│   Django App     │ │   Static Files   │ │   Media Files    │
│   (Gunicorn)     │ │   (Nginx)        │ │   (Nginx)        │
│                  │ │                  │ │                  │
│  ┌────────────┐  │ │                  │ │                  │
│  │  Catalog   │  │ │                  │ │                  │
│  │  Cart      │  │ │                  │ │                  │
│  │  Orders    │  │ │                  │ │                  │
│  │  Payment   │  │ │                  │ │                  │
│  │  Coupons   │  │ │                  │ │                  │
│  └────────────┘  │ │                  │ │                  │
└────────┬─────────┘ └──────────────────┘ └──────────────────┘
         │
         │
┌────────┴────────────────────────────────────────────────────────────────────┐
│                              Celery Workers                                  │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐           │
│  │  Email Worker    │  │  PDF Worker      │  │  Other Workers   │           │
│  │  (Order emails)  │  │  (Invoices)      │  │                  │           │
│  └──────────────────┘  └──────────────────┘  └──────────────────┘           │
└─────────────────────────────────────────────────────────────────────────────┘
         │               │               │
         ▼               ▼               ▼
┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐
│   PostgreSQL     │ │     Redis        │ │    RabbitMQ      │
│   (Database)     │ │   (Cache +       │ │   (Celery        │
│                  │ │    Results)      │ │    Broker)       │
└──────────────────┘ └──────────────────┘ └──────────────────┘
```

## Django Apps

| App | Responsibility |
|-----|----------------|
| **catalog** | Products, categories, search |
| **cart** | Session-based shopping cart |
| **orders** | Order creation and management |
| **payment** | Stripe Checkout integration |
| **coupons** | Discount codes and promotions |
| **accounts** | User authentication, registration, login, profile, email verification |

## Data Model

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Category   │     │   Product   │     │    Order    │
├─────────────┤     ├─────────────┤     ├─────────────┤
│ id (PK)     │     │ id (PK)     │     │ id (PK)     │
│ name        │     │ name        │     │ user_id(FK) │
│ slug        │     │ slug        │     │ status      │
│ description │     │ description │     │ total       │
└─────────────┘     │ price       │     │ coupon_id   │
                    │ category_id │     │ created_at  │
                    │ available   │     └─────────────┘
                    │ created_at  │
                    └─────────────┘
                           │
                           ▼
                    ┌─────────────┐     ┌─────────────┐
                    │    Cart     │     │  CartItem   │
                    ├─────────────┤     ├─────────────┤
                    │ id (PK)     │     │ id (PK)     │
                    │ session_key │     │ cart_id(FK) │
                    │ created_at  │     │ product_id  │
                    └─────────────┘     │ quantity    │
                                        └─────────────┘
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Homepage |
| `/catalog/` | GET | Product listing |
| `/catalog/<slug>/` | GET | Product detail |
| `/cart/` | GET | View cart |
| `/cart/add/<id>/` | POST | Add to cart |
| `/cart/remove/<id>/` | POST | Remove from cart |
| `/checkout/` | GET/POST | Checkout flow |
| `/payment/success/` | GET | Payment success |
| `/payment/webhook/` | POST | Stripe webhook |

## Background Jobs

| Queue | Task | Description |
|-------|------|-------------|
| email | `send_order_email` | Send order confirmation |
| email | `send_invoice_email` | Send PDF invoice |
| pdf | `generate_invoice` | Generate PDF invoice |
| default | Other tasks | General background work |

## Docker Services

| Service | Port | Description |
|---------|------|-------------|
| nginx | 80 | Reverse proxy |
| web | 8000 | Django application |
| celery-worker | — | Background job processor |
| celery-beat | — | Periodic task scheduler |
| flower | 5555 | Celery monitoring |
| postgres | 5432 | Database |
| redis | 6379 (DB 1 for Celery results) | Cache + Celery results — **shared instance** from `~/projects/shared-services` |
| rabbitmq | 5672 | Celery broker |

## Authentication

### Email verification (card d7ac2957)

Local registrations are verified at the app level (django-allauth's own
verification stays off, `ACCOUNT_EMAIL_VERIFICATION = 'none'`).

- `EmailVerificationToken` model (`accounts.models`): one row per link, the
  raw secret is never stored — only `sha256(secret)` hex (`token_hash`),
  with a 72h TTL (`EMAIL_VERIFICATION_TOKEN_TTL`).
- Registration issues a token and sends a plain-text verification email
  (subject "Confirm your email address", from `DEFAULT_FROM_EMAIL`) whose
  body contains the absolute verification URL. A broken mail backend is
  caught and logged — it never turns registration into a 500.
- `GET /accounts/verify-email/<secret>/` classifies the link `ok` /
  `invalid` / `expired`, marks it single-use (`verified_at`), and is
  informational only (does not gate login, shopping, or checkout).
- `POST /accounts/resend-verification/` (login-required, rate-limited via
  the `email_verify` scope) re-sends the link unless the email is already
  verified. Changing the email on the profile issues a fresh token for the
  new address.
- The profile badge shows whether the current email is verified.

### Password reset (card 9ee2accb)

Password reset uses Django's built-in views and default reset email — no
custom model, no migration, and independent of the email-verification flow
above. The reset email goes out from `DEFAULT_FROM_EMAIL`.

- `GET/POST /accounts/password-reset/` (`accounts:password_reset`) renders
  the email form and sends the reset link. The POST is rate-limited per
  client IP via the `password_reset` scope (`RATE_LIMIT_PASSWORD_RESET`,
  default `(5, 3600)`); GETs are never limited.
- `GET /accounts/password-reset/done/` (`accounts:password_reset_done`)
  always answers "if an account exists for that address, a reset link has
  been sent", so unknown addresses are indistinguishable from known ones.
- `GET/POST /accounts/reset/<uidb64>/<token>/`
  (`accounts:password_reset_confirm`) checks the token and sets the new
  password; `post_reset_login = False`, so the user logs in separately.
  Invalid/expired links render the same panel with an explanatory message.
- `GET /accounts/reset/done/` (`accounts:password_reset_complete`)
  confirms the change and links back to `accounts:login`.
- The default reset email reverses the *bare* name
  `password_reset_confirm`, so `ecommerce/urls.py` keeps an unnamespaced
  alias for that name (same path and view as the namespaced route above).
- The login page links to the flow ("Forgot your password?").

### Environment

| Variable | Default | Description |
|----------|---------|-------------|
| `DEFAULT_FROM_EMAIL` | `no-reply@ecommerce.com` | From address for outgoing email, including verification emails. |
| `EMAIL_BACKEND` | `django.core.mail.backends.console.EmailBackend` | Email backend (console in dev/docker; locmem under tests). |
| `EMAIL_VERIFICATION_TOKEN_TTL` | `60 * 60 * 72` (72h) | Time-to-live for verification tokens. |

## Payments

### Idempotency (card 7e732hbh)

Stripe delivers webhooks at-least-once and clients can retry checkout, so
both ends of a payment are guarded without any extra persistence:

- The webhook marks an order paid with one atomic conditional UPDATE
  (`Order.objects.filter(id=order_id, paid=False).update(paid=True, ...)`).
  Only the delivery that flips the row dispatches `payment_completed`, so
  replays and concurrent duplicates are no-ops and never re-queue invoice
  emails. A payment that already landed keeps its stored `stripe_id`
  (first writer wins), and the handler still answers 200 for signature-valid
  replays or vanished orders (400 only for bad signature / bad reference /
  unknown order).
- `payment_process` creates its Checkout Session with
  `idempotency_key='order-<id>'` — deterministic per order, so Stripe dedups
  identical creations for 24h and a crash/retry cannot charge twice.
- `payment_process` short-circuits to `payment:completed` when the order is
  already paid (GET and POST alike): the session id outlives the webhook, so
  a returning user never gets a second Stripe session for a paid order.

### Fraud signals (card 832hc4eg)

Fraud detection is **flag-only**: it never blocks, cancels, or auto-refunds
a payment. The engine (`orders/fraud.py`) evaluates six deterministic,
pure-ORM rules — `velocity_email` (orders sharing the billing email in a
window), `large_total`, `bulk_quantity`, `new_account_high_value` (fresh
account + high total), `identity_mismatch` (checkout email differs from the
account email), and `coupon_velocity` (many orders reusing one coupon) —
each with its own weight.

It runs once per order, inside the webhook's guarded `updated == 1` payment
transition (so Stripe replays never re-evaluate): the score (sum of fired
weights, capped at 100), a boolean flag (`score >= FRAUD_FLAG_THRESHOLD`),
and the comma-joined triggered rule names are persisted onto the Order row
(`fraud_score`, `is_flagged`, `fraud_notes`) via one extra UPDATE — never
`.save()`, so no concurrent field can be stomped. An evaluation failure is
logged only: the order stays paid and the invoice always dispatches.

Windows/thresholds (`FRAUD_WINDOW_HOURS`, `FRAUD_EMAIL_VELOCITY`,
`FRAUD_LARGE_TOTAL`, `FRAUD_BULK_QUANTITY`, `FRAUD_NEW_ACCOUNT_*`,
`FRAUD_COUPON_VELOCITY`, `FRAUD_FLAG_THRESHOLD`) and per-rule weights
(`FRAUD_WEIGHT_<RULE>`) are Django settings read at call time, so
operators and tests can tune them without code changes. Analysts review
flags through the `mark_rules_reviewed` admin action, which clears the flag
and prefixes the notes with `reviewed: ` (score and rule names remain as an
audit trail).

### Refunds (card a3cg42c4)

Refunds are **analyst-initiated** — nothing in the app refunds automatically
(the fraud engine is flag-only and never refunds). The module
`payment/refunds.py` exposes one primitive, `refund_order(order, amount=None)`,
which returns `(True, 'ok')` or `(False, reason)` and never raises: Stripe
errors are caught, logged and returned as a message, so a refund failure can
never surface as a 500.

- **Admin action** `issue_full_refund` on `OrderAdmin` refunds the full
  remaining total of every selected order (one Stripe `Refund.create` per
  order, amount omitted) and reports the reason for every skipped order.
- **Guards**: an unpaid order or an order without a Stripe `payment_intent`
  is *not refundable*, a non-positive partial amount is an *invalid amount*,
  and an order that is already fully refunded locally is *already refunded* —
  the last one is checked *before* the Stripe call, so a double-clicked admin
  action cannot refund twice.
- **State** lives on the order row: `refunded_amount` ($ units, the same
  units as `item.price`), `refund_id` (last Stripe Refund object id) and
  `refunded_at`. The fields are written with a single targeted
  `Order.objects.filter(...).update(...)` — never `.save()` — so a concurrent
  write to another column is never stomped. Partial refunds convert $ to
  integer cents the same way `payment_process` does.
- **Inbound truth**: the webhook also handles `charge.refunded` events, so a
  refund made in the Stripe dashboard is reflected locally (the charge
  carries the cumulative refunded sum). Such an event never issues a refund,
  never re-dispatches the invoice and never evaluates fraud. The
  customer-facing return/refund request flow is a **later card** (4fcgc9e) —
  there is no customer-facing refund URL or view yet.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Database | PostgreSQL | ACID, full-text search |
| Cache | Redis | Fast, pub/sub for Celery |
| Broker | RabbitMQ | Reliable message delivery |
| Payments | Stripe | Industry standard, easy integration |
| PDF | WeasyPrint | HTML/CSS to PDF |
| Auth | Django sessions | Simple, secure by default |

## Security Considerations

- ✅ SECRET_KEY required via Compose `${SECRET_KEY:?...}`
- ✅ SESSION_COOKIE_SECURE and CSRF_COOKIE_SECURE in production
- ✅ ALLOWED_HOSTS enforced when DEBUG=False
- ✅ Rate limiting on checkout/coupon POSTs (429 + Retry-After)
- ⚠️ Default Postgres credentials in docker-compose.yml
