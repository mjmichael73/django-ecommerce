# AGENTS.md — Django E-commerce

## Project Overview

Django 5 e-commerce demo with session-based cart, Stripe Checkout payments, coupon discounts, and Celery background jobs for order emails and PDF invoices (WeasyPrint).

**Stack:** Django 5.0, PostgreSQL, Redis, RabbitMQ, Celery 5, Stripe, WeasyPrint, Gunicorn, Docker Compose

## Quick Start

```bash
# From repository root
cp .env.example .env   # fill SECRET_KEY + POSTGRES_PASSWORD (gitignored)
docker compose build
docker compose up -d  # or make up-d
```

- **App:** http://localhost:8089/
- **RabbitMQ:** http://localhost:15672/ (guest/guest)
- **Flower:** http://localhost:5555/

## Key Commands

```bash
make up-d             # start stack detached (needs shared Postgres: cd ~/projects/shared-services && docker compose up -d postgres)
make up-d-proxy       # start with nginx reverse proxy on :8080
make migrate          # run migrations
make seed             # load demo data (superuser: superadmin/password)
make test             # run Django tests (requires stack running)
make check-deploy     # Django deployment checks (DEBUG=False)
make down             # stop stack (keep volumes)
make down-volumes     # stop stack and remove volumes
make logs-web         # follow web logs
make shell            # Django shell in web container
```

## Project Structure

```
django-ecommerce/
├── be/
│   └── ecommerce/           # Django project
│       ├── accounts/        # User authentication
│       ├── cart/            # Session-based shopping cart
│       ├── coupons/         # Discount codes
│       ├── ecommerce/       # Project settings, Celery config
│       ├── orders/          # Order management + tasks
│       ├── payment/         # Stripe Checkout + webhook
│       └── shop/            # Products, categories
├── docker/
│   ├── nginx/default.conf   # Reverse proxy config
│   └── entrypoint.sh        # Wait for DB, migrate, collectstatic
├── docker-compose.yml       # Full stack (shared Postgres+Redis, RabbitMQ, etc.)
├── Dockerfile               # App image with WeasyPrint deps
├── Makefile                 # Common commands
└── .env.example             # Environment template
```

## Django Apps

| App | Responsibility |
|-----|----------------|
| **shop** | Products, categories, catalog views |
| **cart** | Session-based cart (no DB models) |
| **orders** | Order creation, management, PDF invoices |
| **payment** | Stripe Checkout sessions, webhook handling |
| **coupons** | Discount codes with percentage discounts |
| **accounts** | User authentication (registration, login) |

## Data Models

- **Category** → **Product** (FK)
- **Order** → **OrderItem** (FK) → **Product** (FK)
- **Order** ← **Coupon** (FK, optional)
- **Cart** is session-based (no DB models)

## Testing

```bash
# With Docker Compose (requires stack running)
make up-d
make test

# Local (venv with deps)
cd be/ecommerce
SECRET_KEY=... python manage.py test ecommerce.tests orders.tests payment.tests coupons.tests accounts.tests shop.tests -v2
```

PostgreSQL is the **shared instance** from `~/projects/shared-services` (external network `shared-services-network`, database `ecommerce`); start it with `cd ~/projects/shared-services && docker compose up -d postgres`.

Tests target: checkout, Stripe webhook (signed payloads), coupon edge cases.

## Celery Workers

- **celery-worker**: Background jobs (emails, PDF generation)
- **celery-beat**: Periodic tasks (not configured by default)
- **Flower**: Monitoring at :5555

Healthcheck: `celery -A ecommerce inspect ping`

## Stripe Integration

- **Checkout Session**: Created on order placement
- **Webhook**: `/payment/webhook/` handles `checkout.session.completed`
- **Test keys**: Set `STRIPE_*` in `.env` for local development
- **Stripe CLI**: Use for local webhook forwarding

## PDF Invoices (WeasyPrint)

- Stylesheet: `shop/static/css/pdf.css`
- Resolved via `ecommerce.pdf_stylesheet.pdf_invoice_css_path()`
- Uses `STATIC_ROOT` after `collectstatic`, falls back to staticfiles finders
- CI verifies stylesheet exists

## Environment Variables

Key variables (see `.env.example`):

| Variable | Required | Description |
|----------|----------|-------------|
| `SECRET_KEY` | Yes | Django secret (Compose requires it) |
| `DEBUG` | No | Default: True |
| `ALLOWED_HOSTS` | Prod | Comma-separated hosts |
| `POSTGRES_DB` / `POSTGRES_USER` | No | Default: ecommerce/ecommerce (shared-services instance) |
| `POSTGRES_PASSWORD` | Yes | Required — no default; must match your Postgres container |
| `STRIPE_*` | Prod | Payment keys |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Optional | Google OAuth2 (django-allauth). BOTH set → social login enabled; blank/one missing → disabled. Callback URL: `/accounts/google/login/callback/` |
| `DJANGO_BEHIND_PROXY` | Proxy | Trust X-Forwarded headers |
| `CSRF_TRUSTED_ORIGINS` | Prod | HTTPS origins for CSRF |
| `SECURE_SSL_REDIRECT` | Prod | HTTPS redirect |
| `REDIS_AOF` | No | Shared Redis persistence policy (managed in shared-services) |

## Production Deployment

1. Set `DEBUG=False` and configure `ALLOWED_HOSTS`
2. Use strong `SECRET_KEY`, `POSTGRES_PASSWORD`
3. Set live `STRIPE_*` keys and `STRIPE_WEBHOOK_SECRET`
4. Configure real email backend
5. Terminate TLS at nginx (use `reverse-proxy` profile)
6. Run `make check-deploy` and fix issues
7. See [docs/production.md](docs/production.md) for full guide

## Security Notes

- `SECRET_KEY` and `POSTGRES_PASSWORD` required via Compose `${VAR:?...}` — no default credentials in the repo
- `SESSION_COOKIE_SECURE` and `CSRF_COOKIE_SECURE` in production
- `ALLOWED_HOSTS` enforced when `DEBUG=False`
- Stripe handles PCI compliance (card data never stored)
- Webhook signature verification implemented
- CSRF token rotation on every identity change (login, registration, logout) via `ecommerce/csrf_rotation.py`; failed logins keep the token
- POST rate limits on checkout/coupon (429 + `Retry-After`), counters in Redis cache DB 2 of the shared instance
- Content Security Policy headers enforced on every response via `ecommerce/csp_middleware.py` and `CSP_POLICY` in settings (card f3d54f76)
- Social authentication (Google, django-allauth) is env-gated: only wired when both `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` are set (`ecommerce/social_auth_config.py`); secrets never in the repo; allauth ships its own migrations (applied by the entrypoint/boot migrate when enabled). See `docs/security.md` (card hhde4h24)

## Useful Files

- `be/ecommerce/ecommerce/settings.py` — Django settings
- `be/ecommerce/ecommerce/celery.py` — Celery config
- `be/ecommerce/ecommerce/pdf_stylesheet.py` — PDF stylesheet resolution
- `docker-compose.yml` — Service definitions
- `docker/nginx/default.conf` — Nginx reverse proxy
- `.github/workflows/ci.yml` — CI pipeline (collectstatic check + tests; Docker image build on every run, GHCR publish on `main`)