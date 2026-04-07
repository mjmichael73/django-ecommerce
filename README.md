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
| Web   | Django 5.0, Gunicorn + WhiteNoise in Docker; SQLite (local) or PostgreSQL (Compose) |
| Queue | Celery 5 + RabbitMQ; Redis for Celery results (Compose)                         |
| Pay   | [Stripe](https://stripe.com/)                                                 |
| PDF   | [WeasyPrint](https://weasyprint.org/)                                         |
| Ops   | Optional [Flower](https://flower.readthedocs.io/) for Celery monitoring       |

## Prerequisites

- Python 3.10+ (recommended; match your environment with Django 5.0)
- [Docker](https://docs.docker.com/get-docker/) (optional for local dev; required for the full Compose stack below)

## Project layout

- `be/requirements.txt` — Python dependencies
- `be/ecommerce/` — Django project (`manage.py`, apps: `shop`, `cart`, `orders`, `payment`, `coupons`)
- `Dockerfile` — app image (WeasyPrint system libs + Python)
- `docker-compose.yml` — PostgreSQL, Redis, RabbitMQ, Gunicorn web, Celery worker
- `docker/entrypoint.sh` — wait for Postgres, `migrate`, `collectstatic`
- `.env.example` — copy to `.env` to override secrets and toggles for Compose

## Docker (full stack)

From the **repository root**:

```bash
cp .env.example .env   # optional; defaults are embedded in compose for quick starts
docker compose build
docker compose up
```

- **App:** http://localhost:8000/  
- **RabbitMQ management:** http://localhost:15672/ (guest / guest)

The `web` service runs Gunicorn; the first start runs migrations and `collectstatic`. Uploaded media is stored in the `media_data` volume. Set `STRIPE_*` and `SECRET_KEY` in `.env` before any real traffic.

Optional **Flower** (same image, one-off):

```bash
docker compose run --rm -p 5555:5555 celery-worker \
  celery -A ecommerce flower --address=0.0.0.0 --port=5555
```

Then open http://localhost:5555/ (add a dedicated `flower` service if you prefer it always on).

## Setup (local Python, no Compose)

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

Set environment variables (see `.env.example`), or export them in your shell before `runserver`:

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

## Message broker (RabbitMQ) without full Compose

If you run Django on the host but only need RabbitMQ, start the broker alone:

```bash
docker compose up -d rabbitmq
```

Default management login: **guest** / **guest** — http://localhost:15672  

With local Python, Celery defaults to `CELERY_BROKER_URL=amqp://guest:guest@127.0.0.1:5672//`. Set `CELERY_RESULT_BACKEND` (e.g. `redis://127.0.0.1:6379/0`) only if you use a Redis result backend.

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

By default `EMAIL_BACKEND` is the console backend unless you set `EMAIL_BACKEND` in the environment. Use SMTP or a transactional provider for real delivery.

## TODO

Proposed improvements (not implemented here; ideas for evolving the project):

1. **Configuration** — Tighten Compose defaults (no insecure `SECRET_KEY` in repo workflows) and add a production env checklist.
2. **Production readiness** — Harden `ALLOWED_HOSTS`, `DEBUG`, HTTPS, reverse proxy (e.g. Traefik or nginx) in front of Gunicorn.
3. **Celery operations** — Add a dedicated Flower service or healthchecks for workers; optional Redis persistence policy for result backend.
4. **Stripe webhook locally** — Document or script Stripe CLI forwarding for reliable local webhook testing.
5. **WeasyPrint + static files** — Ensure `static/css/pdf.css` is available at `STATIC_ROOT` in all environments (e.g. `collectstatic` in CI/deploy) so PDF generation does not depend on dev-only paths.
6. **Tests** — Add pytest (or Django’s test runner) coverage for checkout, webhooks (signed events), and coupon edge cases.
7. **Observability** — Structured logging, request IDs, and Celery task failure alerts (e.g. Sentry).
8. **UX and catalog** — Search/faceted navigation, inventory flags, product images pipeline, and responsive polish.
9. **Security** — Rate limiting on checkout and coupon application, CSRF/session review for payment return URLs, and periodic dependency updates.
10. **Containerization** — Multi-stage image build, non-root user, and optional nginx sidecar for TLS and caching.

## License

If you publish this repository, add a `LICENSE` file; otherwise treat licensing as unspecified until you choose one.
