# Django E-commerce

A Django 5 demo shop with a session-based cart, checkout, **Stripe Checkout** payments, **coupon** discounts, and **Celery** background jobs for order emails and PDF invoices (WeasyPrint).

## Features

- **Catalog** — products and categories
- **Cart** — session-backed cart with add/remove/update flows
- **Accounts** — registration/login/logout, plus optional "Continue with Google" social login via django-allauth (enabled only when `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` are set; see [docs/security.md](docs/security.md))
- **Orders** — checkout and order management (admin includes Stripe dashboard links when `stripe_id` is set)
- **Payments** — Stripe Checkout Session + webhook handling
- **Coupons** — apply discounts at checkout
- **Async tasks** — order confirmation email; post-payment email with PDF invoice attachment

## Stack

| Layer | Technology                                                                     |
| ----- | ------------------------------------------------------------------------------ |
| Web   | Django 5.0, Gunicorn + WhiteNoise in Docker; SQLite (local) or PostgreSQL (Compose) |
| Queue | Celery 5 + RabbitMQ; Redis (shared instance) for Celery results            |
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
- `docker-compose.yml` — RabbitMQ, Gunicorn, Celery worker (healthcheck), **Flower**, optional **nginx** (`reverse-proxy` profile). **PostgreSQL and Redis are the shared instances** from `~/projects/shared-services` (external network `shared-services-network`, database `ecommerce`) — start them with `cd ~/projects/shared-services && docker compose up -d postgres redis`
- `docker/nginx/default.conf` — reverse-proxy headers for Gunicorn (`X-Forwarded-*`)
- `docker/entrypoint.sh` — wait for Postgres, `migrate`, `collectstatic`
- `.env.example` — complete template of every credential/secret the system needs; copy it to `.env` (gitignored — never commit real secrets)
- `be/ecommerce/ecommerce/db_config.py` — builds the Postgres `DATABASES` config; refuses to run without an explicit `POSTGRES_PASSWORD`
- `ecommerce/pdf_stylesheet.py` — resolves `css/pdf.css` for WeasyPrint (`STATIC_ROOT` or staticfiles finders)
- `.github/workflows/ci.yml` — runs `collectstatic` + stylesheet check, and **`manage.py test`** for `ecommerce` (env/credential guards), `orders`, `payment`, `coupons`; also builds the Docker image on every run and **publishes it to GHCR on `main`** (`latest` + `sha-<short>`)

## Docker (full stack)

`SECRET_KEY` **and** `POSTGRES_PASSWORD` are **required**: Compose does not ship insecure defaults. From the **repository root**:

```bash
cp .env.example .env   # then fill SECRET_KEY + POSTGRES_PASSWORD (openssl rand -base64 48)
docker compose build
docker compose up
```

Use **`make up-d`** or **`make up-detached`** for a **detached** stack. Do **not** run `make up -d`: GNU Make treats `-d` as debug mode, so Compose never receives `-d`, and an empty `SECRET_KEY` in `.env` will still break `docker compose up`.

- **App:** http://localhost:8089/  
- **RabbitMQ management:** http://localhost:15672/ (guest / guest)

The `web` service runs Gunicorn; the first start runs migrations and `collectstatic`. Uploaded media is stored in the `media_data` volume. Set **`STRIPE_*`** in `.env` before taking real payments.

### Reverse proxy (nginx in front of Gunicorn)

For the same layout you would use in production (clients → TLS/reverse proxy → app), start Compose with the **`reverse-proxy`** profile. **nginx** listens on **host port 8080** and forwards to Gunicorn on the internal network; Gunicorn stays on **8000** for direct access during development.

```bash
make up-d-proxy
# Shop via proxy: http://localhost:8080/   — still available directly: http://localhost:8089/
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

### Invoice PDFs (WeasyPrint)

The invoice stylesheet is **`shop/static/css/pdf.css`**. **`ecommerce.pdf_stylesheet.pdf_invoice_css_path()`** uses **`STATIC_ROOT/css/pdf.css`** after **`collectstatic`**, and falls back to staticfiles **finders** when `STATIC_ROOT` is not populated (typical local **`runserver`**). Docker runs **`collectstatic`** at image build and in the entrypoint. CI (**`.github/workflows/ci.yml`**) checks the file exists; with a local venv you can run **`make verify-pdf-static`**.

### Redis and the Celery result backend

**Redis** is the **shared instance** from `~/projects/shared-services` (container `redis` on `shared-services-network` — the same network this stack's services join for Postgres). This project uses **DB 1** for the Celery result backend (`redis://redis:6379/1`) and **DB 2** for the Django cache (rate limiting, `redis://redis:6379/2`) so its keys stay separate from other projects on the shared instance; no local Redis service or volume is defined in this Compose file, and its persistence policy (AOF/RDB) is managed in `~/projects/shared-services`.

`make down-volumes` removes the media volume. (Postgres and Redis data live in the shared instance and are untouched.)

### Running tests (Docker Compose)

Tests use Django’s built-in runner (**not pytest**) and target **checkout**, **Stripe webhook** (signed payload handling via mocked `construct_event`), and **coupon** edge cases.

With the stack running:

```bash
make up-d
make test
```

**`make test`** uses **`docker compose run`** so **`./be/ecommerce`** is bind-mounted into a short-lived **`web`** container (your working tree, no image rebuild). Dependencies (**`rabbitmq`**, plus the **shared `redis`**) must already be up (**`make up-d`**), and the **shared PostgreSQL** must be running. On the host (venv with deps): **`cd be/ecommerce && SECRET_KEY=… python manage.py test`** the same labels. CI runs the same suite in **`.github/workflows/ci.yml`** (`test` job).

### CI / automation

Export `SECRET_KEY` in the environment (or provide a `.env` file) before `docker compose` — the same `:?` rule applies. Example: `SECRET_KEY="$(openssl rand -base64 48)" docker compose up -d` for ephemeral test runs.

**Compose file parsing:** Any `docker compose` command expands `${SECRET_KEY:?…}`. If `.env` has no `SECRET_KEY` yet, raw CLI calls (e.g. `docker compose build`) fail. **`make`** targets that are safe without a real secret (`build`, `down`, `logs`, `ps`, `restart`, `exec` helpers, …) set a **parse-only placeholder** for that invocation. **`make up`** / **`make up-d`** still require a real key—create `.env` first (`cp .env.example .env`).

## Production environment checklist

Use this before pointing a real domain or live Stripe keys at the app. Not every item may apply to your host, but nothing below should be surprising in production.

| Area | Check |
|------|--------|
| **Secrets** | `SECRET_KEY` is long, random, unique per environment; **never** committed (`.env` is gitignored). Rotate if it ever leaked. |
| **Django** | `DEBUG=False`. `ALLOWED_HOSTS` lists only your domain(s) (never `*`). Set `CSRF_TRUSTED_ORIGINS` for each HTTPS origin. Use `DJANGO_BEHIND_PROXY=1` behind nginx/Traefik; run `make check-deploy`. |
| **Database** | `POSTGRES_PASSWORD` is strong — no default is shipped, and the app refuses to start a Postgres connection without one. Restrict Postgres to internal networks only. |
| **Stripe** | Live `STRIPE_*` keys and `STRIPE_WEBHOOK_SECRET`; webhook URL uses HTTPS and matches the deployed host. |
| **Email** | Real `EMAIL_*` / SMTP or provider backend — not the console backend. |
| **TLS** | Terminate TLS at nginx (this repo includes a proxy profile), Traefik, or your LB; do not expose Gunicorn publicly. Prefer redirects to HTTPS at the proxy; see `SECURE_SSL_REDIRECT` in `.env.example`. |
| **Broker / cache** | RabbitMQ and Redis are not exposed publicly by this stack; the shared Redis container lives on `shared-services-network` and its persistence policy is managed in `~/projects/shared-services`. |
| **Rate limits** | Checkout and coupon-apply POSTs are limited per client IP (10 per hour by default — `RATE_LIMIT_CHECKOUT` / `RATE_LIMIT_COUPON`), returning 429 with `Retry-After`. Counters live in the Django cache: Redis DB 2 of the shared instance under Compose, in-process memory otherwise. |
| **Content Security Policy** | A `Content-Security-Policy` header is enforced on every response (`CSP_POLICY` in settings, `ecommerce/csp_middleware.py`); optional `CSP_REPORT_URI` for violation reporting (`.env.example`). |
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

For local webhook testing without any public deployment, see [docs/stripe-cli.md](docs/stripe-cli.md) — it documents Stripe CLI forwarding to `localhost:8089/payment/webhook/`.

### 5. Run the web app

From `be/ecommerce`:

```bash
python manage.py runserver
```

Admin: http://127.0.0.1:8089/admin/  
Shop: http://127.0.0.1:8089/

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

1. ~~**Configuration** — Tighten Compose defaults (no insecure `SECRET_KEY` or Postgres credentials in repo workflows) and add a production env checklist.~~ (Done: required `SECRET_KEY` + `POSTGRES_PASSWORD` with no shipped defaults, `.env` from `.env.example`, checklist above.)
2. ~~**Production readiness** — Apply the checklist; harden `ALLOWED_HOSTS`, `DEBUG`, HTTPS, reverse proxy in front of Gunicorn.~~ (Done: strict `ALLOWED_HOSTS` when `DEBUG=False`, proxy-aware TLS flags, optional nginx profile, `make check-deploy`.)
3. ~~**Celery operations** — Add a dedicated Flower service or healthchecks for workers; optional Redis persistence policy for result backend.~~ (Done: Flower in default compose, worker `inspect ping` healthcheck; Celery results use DB 1 of the **shared** Redis instance.)
4. ~~**Stripe webhook locally** — Document or script Stripe CLI forwarding for reliable local webhook testing.~~ (Done: [docs/stripe-cli.md](docs/stripe-cli.md) — `stripe listen` forwarding to `localhost:8089/payment/webhook/`.)
5. ~~**WeasyPrint + static files** — Ensure `static/css/pdf.css` is available at `STATIC_ROOT` in all environments (e.g. `collectstatic` in CI/deploy) so PDF generation does not depend on dev-only paths.~~ (Done: `ecommerce/pdf_stylesheet.py`, CI job, `make verify-pdf-static`.)
6. ~~**Tests** — Add pytest (or Django’s test runner) coverage for checkout, webhooks (signed events), and coupon edge cases.~~ (Done: Django `TestCase` modules; `make test` via Compose; CI job.)
7. ~~**Observability** — Structured logging, request IDs, and Celery task failure alerts (e.g. Sentry).~~ (Done: JSON structured logging with `X-Request-Id` correlation IDs, sensitive-data redaction, and Celery task propagation — card 6cc44f8g. Remaining: Sentry/Prometheus/Grafana.)
8. **UX and catalog** — Search/faceted navigation, inventory flags, product images pipeline, and responsive polish.
9. **Security** — CSRF/session review for payment return URLs, and periodic dependency updates.
10. **Containerization** — Multi-stage image build, non-root user, and optional nginx sidecar for TLS and caching.

## Documentation

See the [docs/](docs/) folder for detailed documentation:

- [Architecture](docs/architecture.md) — System architecture, data models, and design decisions
- [Testing](docs/testing.md) — Testing strategy, unit tests, integration tests, and E2E tests
- [Security](docs/security.md) — Security measures, configuration, and checklists
- [Stripe CLI](docs/stripe-cli.md) — Local webhook forwarding with the Stripe CLI
- [Production](docs/production.md) — Production deployment guide and checklist
- [Roadmap](docs/roadmap.md) — Future improvements and feature roadmap

## License

If you publish this repository, add a `LICENSE` file; otherwise treat licensing as unspecified until you choose one.
