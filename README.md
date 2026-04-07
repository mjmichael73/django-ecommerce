# Django E-commerce

A Django 5 demo shop with a session-based cart, checkout, **Stripe Checkout** payments, **coupon** discounts, and **Celery** background jobs for order emails and PDF invoices (WeasyPrint).

## Features

- **Catalog** — products and categories
- **Cart** — session-backed cart with add/remove/update flows
- **Orders** — checkout and order management (admin includes Stripe dashboard links when `stripe_id` is set)
- **Payments** — Stripe Checkout Session + webhook handling
- **Coupons** — apply discounts at checkout
- **Async tasks** — order confirmation email; post-payment email with PDF invoice attachment

## Stack

| Layer | Technology                                                                     |
| ----- | ------------------------------------------------------------------------------ |
| Web   | Django 5.0, SQLite (default), session cart                                    |
| Queue | Celery 5 + RabbitMQ (via Docker Compose)                                      |
| Pay   | [Stripe](https://stripe.com/)                                                 |
| PDF   | [WeasyPrint](https://weasyprint.org/)                                         |
| Ops   | Optional [Flower](https://flower.readthedocs.io/) for Celery monitoring       |

## Prerequisites

- Python 3.10+ (recommended; match your environment with Django 5.0)
- [Docker](https://docs.docker.com/get-docker/) (optional, for RabbitMQ)

## Project layout

- `be/requirements.txt` — Python dependencies
- `be/ecommerce/` — Django project (`manage.py`, apps: `shop`, `cart`, `orders`, `payment`, `coupons`)
- `docker-compose.yml` — RabbitMQ with management UI

## Setup

### 1. Python environment

```bash
cd be/ecommerce
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r ../requirements.txt
```

### 2. WeasyPrint system libraries

WeasyPrint needs OS-level dependencies (Cairo, Pango, etc.). Follow the official guide:  
https://doc.courtbouillon.org/weasyprint/stable/first_steps.html  

**Windows (summary):** install [MSYS2](https://www.msys2.org/), then in the MSYS2 terminal install GTK/GObject packages and add the MinGW `bin` directory to your `PATH` as described in that documentation; recreate the virtualenv and reinstall requirements if needed.

### 3. Database

```bash
python manage.py migrate
python manage.py createsuperuser   # optional, for admin
```

### 4. Stripe (payments & webhooks)

In `be/ecommerce/ecommerce/settings.py`, set (or override via your own env-loader in production):

- `STRIPE_PUBLISHABLE_KEY`
- `STRIPE_SECRET_KEY`
- `STRIPE_WEBHOOK_SECRET` — used by `payment/views/stripe_webhook_view.py`

Use Stripe test keys for local development and configure the webhook endpoint to point at your tunneled/public URL (e.g. `/payment/webhook/`).

### 5. Run the web app

From `be/ecommerce`:

```bash
python manage.py runserver
```

Admin: http://127.0.0.1:8000/admin/  
Shop: http://127.0.0.1:8000/

## Message broker (RabbitMQ)

Start RabbitMQ (management UI on port **15672**):

```bash
# from repository root
docker compose up -d
```

Default management login: **guest** / **guest** — http://localhost:15672  

If Celery cannot connect, ensure the broker URL matches your setup (default AMQP on `localhost:5672` is typical for local RabbitMQ). You can set `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` in Django settings under the `CELERY_` namespace if you need explicit configuration.

## Celery worker

From `be/ecommerce` with the same virtualenv and `DJANGO_SETTINGS_MODULE` implied by `manage.py`:

```bash
celery -A ecommerce worker -l info
```

**Windows:** use a pool that supports your platform, for example:

```bash
celery -A ecommerce worker -l info -P solo
```

## Flower (optional)

Monitor tasks and workers:

```bash
celery -A ecommerce flower
```

## Email in development

`EMAIL_BACKEND` is set to the console backend, so outgoing mail is printed to the terminal rather than delivered. Switch to SMTP or a transactional provider for real delivery.

## TODO

Proposed improvements (not implemented here; ideas for evolving the project):

1. **Configuration** — Move secrets (`SECRET_KEY`, Stripe keys, broker URL) to environment variables and document a `.env.example`; keep defaults only for local dev.
2. **Production readiness** — Harden `ALLOWED_HOSTS`, `DEBUG`, HTTPS, static/media hosting, and use PostgreSQL instead of SQLite for concurrent writes.
3. **Celery settings** — Declare `CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND` explicitly in settings; add Redis to `docker-compose.yml` if you want a result backend or caching layer.
4. **Stripe webhook locally** — Document or script Stripe CLI forwarding for reliable local webhook testing.
5. **WeasyPrint + static files** — Ensure `static/css/pdf.css` is available at `STATIC_ROOT` in all environments (e.g. `collectstatic` in CI/deploy) so PDF generation does not depend on dev-only paths.
6. **Tests** — Add pytest (or Django’s test runner) coverage for checkout, webhooks (signed events), and coupon edge cases.
7. **Observability** — Structured logging, request IDs, and Celery task failure alerts (e.g. Sentry).
8. **UX and catalog** — Search/faceted navigation, inventory flags, product images pipeline, and responsive polish.
9. **Security** — Rate limiting on checkout and coupon application, CSRF/session review for payment return URLs, and periodic dependency updates.
10. **Containerization** — Multi-stage Dockerfile for the Django app plus compose services for web, worker, and broker for one-command local and staging environments.

## License

If you publish this repository, add a `LICENSE` file; otherwise treat licensing as unspecified until you choose one.
