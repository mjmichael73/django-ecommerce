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
| Ops   | [Flower](https://flower.readthedocs.io/) on :5555 with the default stack; optional nginx `reverse-proxy` profile |

## Prerequisites

- Python 3.10+ (recommended; match your environment with Django 5.0)
- [Docker](https://docs.docker.com/get-docker/) (optional for local dev; required for the full Compose stack below)

## Project layout

- `be/requirements.txt` — Python dependencies
- `be/ecommerce/` — Django project (`manage.py`, apps: `shop`, `cart`, `orders`, `payment`, `coupons`)
- `Dockerfile` — app image (WeasyPrint system libs + Python)
- `docker-compose.yml` — PostgreSQL, Redis (optional AOF volume), RabbitMQ, Gunicorn, Celery worker (healthcheck), **Flower**, optional **nginx** (`reverse-proxy` profile)
- `docker/nginx/default.conf` — reverse-proxy headers for Gunicorn (`X-Forwarded-*`)
- `docker/entrypoint.sh` — wait for Postgres, `migrate`, `collectstatic`
- `.env.example` — template for `.env` (never commit real secrets)
- `scripts/bootstrap_env.py` — used by `make env` to create `.env` and a strong `SECRET_KEY`
- `ecommerce/pdf_stylesheet.py` — resolves `css/pdf.css` for WeasyPrint (`STATIC_ROOT` or staticfiles finders)
- `.github/workflows/ci.yml` — runs `collectstatic` and checks `static/css/pdf.css` exists

## Docker (full stack)

`SECRET_KEY` is **required**: Compose does not ship an insecure default. From the **repository root**:

```bash
make env              # creates .env from .env.example and generates SECRET_KEY if needed
docker compose build
docker compose up
```

Use **`make up-d`** or **`make up-detached`** for a **detached** stack. Do **not** run `make up -d`: GNU Make treats `-d` as debug mode, so Compose never receives `-d`, and an empty `SECRET_KEY` in `.env` will still break `docker compose up`.

- **App:** http://localhost:8000/  
- **RabbitMQ management:** http://localhost:15672/ (guest / guest)

The `web` service runs Gunicorn; the first start runs migrations and `collectstatic`. Uploaded media is stored in the `media_data` volume. Set **`STRIPE_*`** in `.env` before taking real payments.

### Reverse proxy (nginx in front of Gunicorn)

For the same layout you would use in production (clients → TLS/reverse proxy → app), start Compose with the **`reverse-proxy`** profile. **nginx** listens on **host port 8080** and forwards to Gunicorn on the internal network; Gunicorn stays on **8000** for direct access during development.

```bash
make up-d-proxy
# Shop via proxy: http://localhost:8080/   — still available directly: http://localhost:8000/
```

Set **`DJANGO_BEHIND_PROXY=1`** in `.env` when traffic reaches Django through this proxy so `request.is_secure()`, redirects, and CSRF use **`X-Forwarded-Proto`** / **`X-Forwarded-Host`** correctly. For a TLS-terminated proxy in production, set **`CSRF_TRUSTED_ORIGINS`** to your **`https://…`** origins.

For real HTTPS, configure nginx with `listen 443 ssl` and publish **80/443** only on nginx; keep **Gunicorn on port 8000** off the public Internet. Set **`DJANGO_BEHIND_PROXY=1`** only when **every** request to Gunicorn comes through that trusted proxy (typical Docker internal network); if Gunicorn is reachable directly from untrusted clients, do not enable it—`X-Forwarded-Proto` could be forged.

### Production-style Django settings

With **`DEBUG=False`**, settings enforce:

- **`ALLOWED_HOSTS`** is required and must not be `*`.
- **`SESSION_COOKIE_SECURE`** / **`CSRF_COOKIE_SECURE`** default to on (overridable via env).
- **`SECURE_SSL_REDIRECT`** defaults to off so HTTP→HTTPS is usually handled at nginx; set **`SECURE_SSL_REDIRECT=true`** only if Django is the TLS edge.
- Optional **HSTS** via **`SECURE_HSTS_SECONDS`** (and related flags in `.env.example`).

After setting production-like variables in `.env`, run:

```bash
make check-deploy
```

Fix any issues reported by `manage.py check --deploy`.

### Celery worker health and Flower

- **`celery-worker`** exposes a Docker **healthcheck** (`celery inspect ping`) so worker status shows in `docker compose ps`.
- **Flower** starts with the default stack (**`make up-d`**). It **replaces the image entrypoint** (no migrate/collectstatic). **Dashboard:** http://localhost:5555/

If Flower was not created with an older Compose file, run **`make flower`** or **`docker compose up -d flower`**.

Combine with nginx when needed: `docker compose --profile reverse-proxy up -d`.

### Redis and the Celery result backend

The **Redis** service uses a named volume **`redis_data`** and, by default (**`REDIS_AOF=yes`**), runs with **AOF** (`appendonly yes`, `appendfsync everysec`) so result-backend data survives container restarts. For a **fully ephemeral** Redis (dev only), set **`REDIS_AOF=no`** in `.env`; Redis then runs without AOF/RDB persistence—results are lost when the container is recreated.

`make down-volumes` removes **`redis_data`** along with Postgres and media volumes.

### CI / automation

Export `SECRET_KEY` in the environment (or provide a `.env` file) before `docker compose` — the same `:?` rule applies. Example: `SECRET_KEY="$(openssl rand -base64 48)" docker compose up -d` for ephemeral test runs.

**Compose file parsing:** Any `docker compose` command expands `${SECRET_KEY:?…}`. If `.env` has no `SECRET_KEY` yet, raw CLI calls (e.g. `docker compose build`) fail. **`make`** targets that are safe without a real secret (`build`, `down`, `logs`, `ps`, `restart`, `exec` helpers, …) set a **parse-only placeholder** for that invocation. **`make up`** / **`make up-d`** still require a real key—run **`make env`** first.

## Production environment checklist

Use this before pointing a real domain or live Stripe keys at the app. Not every item may apply to your host, but nothing below should be surprising in production.

| Area | Check |
|------|--------|
| **Secrets** | `SECRET_KEY` is long, random, unique per environment; **never** committed (`.env` is gitignored). Rotate if it ever leaked. |
| **Django** | `DEBUG=False`. `ALLOWED_HOSTS` lists only your domain(s) (never `*`). Set `CSRF_TRUSTED_ORIGINS` for each HTTPS origin. Use `DJANGO_BEHIND_PROXY=1` behind nginx/Traefik; run `make check-deploy`. |
| **Database** | `POSTGRES_PASSWORD` is strong and not the sample `ecommerce` value. Restrict Postgres to internal networks only. |
| **Stripe** | Live `STRIPE_*` keys and `STRIPE_WEBHOOK_SECRET`; webhook URL uses HTTPS and matches the deployed host. |
| **Email** | Real `EMAIL_*` / SMTP or provider backend — not the console backend. |
| **TLS** | Terminate TLS at nginx (this repo includes a proxy profile), Traefik, or your LB; do not expose Gunicorn publicly. Prefer redirects to HTTPS at the proxy; see `SECURE_SSL_REDIRECT` in `.env.example`. |
| **Broker / cache** | RabbitMQ and Redis are not exposed publicly; change default broker credentials if ports are reachable. Redis persistence (`REDIS_AOF`, `redis_data` volume) is on by default for the result backend. |
| **Media & static** | Media volume or object storage is backed up; understand who can read/write `MEDIA_ROOT`. |
| **Process** | Run `python manage.py check --deploy` in the target environment and fix reported issues. |
| **Dependencies** | Pin or lock images and Python packages; plan security updates. |

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

## Flower (optional, local Python)

With Docker, Flower is already on **http://localhost:5555/** after **`make up-d`**. On the host only:

```bash
cd be/ecommerce && celery -A ecommerce flower
```

## Email in development

By default `EMAIL_BACKEND` is the console backend unless you set `EMAIL_BACKEND` in the environment. Use SMTP or a transactional provider for real delivery.

## TODO

Proposed improvements (not implemented here; ideas for evolving the project):

1. ~~**Configuration** — Tighten Compose defaults (no insecure `SECRET_KEY` in repo workflows) and add a production env checklist.~~ (Done: required `SECRET_KEY`, `make env`, checklist above.)
2. ~~**Production readiness** — Apply the checklist; harden `ALLOWED_HOSTS`, `DEBUG`, HTTPS, reverse proxy in front of Gunicorn.~~ (Done: strict `ALLOWED_HOSTS` when `DEBUG=False`, proxy-aware TLS flags, optional nginx profile, `make check-deploy`.)
3. ~~**Celery operations** — Add a dedicated Flower service or healthchecks for workers; optional Redis persistence policy for result backend.~~ (Done: Flower in default compose, worker `inspect ping` healthcheck; `REDIS_AOF` + `redis_data` volume.)
4. **Stripe webhook locally** — Document or script Stripe CLI forwarding for reliable local webhook testing.
5. ~~**WeasyPrint + static files** — Ensure `static/css/pdf.css` is available at `STATIC_ROOT` in all environments (e.g. `collectstatic` in CI/deploy) so PDF generation does not depend on dev-only paths.~~ (Done: `ecommerce/pdf_stylesheet.py`, CI job, `make verify-pdf-static`.)
6. **Tests** — Add pytest (or Django’s test runner) coverage for checkout, webhooks (signed events), and coupon edge cases.
7. **Observability** — Structured logging, request IDs, and Celery task failure alerts (e.g. Sentry).
8. **UX and catalog** — Search/faceted navigation, inventory flags, product images pipeline, and responsive polish.
9. **Security** — Rate limiting on checkout and coupon application, CSRF/session review for payment return URLs, and periodic dependency updates.
10. **Containerization** — Multi-stage image build, non-root user, and optional nginx sidecar for TLS and caching.

## License

If you publish this repository, add a `LICENSE` file; otherwise treat licensing as unspecified until you choose one.
