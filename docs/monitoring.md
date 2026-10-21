# Monitoring — Django E-commerce

## Overview

Observability for the Django e-commerce demo is built on three layers:

| Observability layer | Card | Description |
|---------------------|------|-------------|
| Structured logging | 6cc44f8g | JSON log output with `X-Request-Id` correlation IDs, sensitive-data redaction, Celery task propagation |
| Prometheus metrics | g37aggbd | Bounded-cardinality HTTP, order, and Celery metrics exposed at `/metrics` + DB-probed `/health` |
| Grafana dashboards | 3c3b2e3a | Pre-built dashboards for HTTP, business, and Celery metrics; Prometheus + Grafana via optional Compose profile |
| Sentry error tracking | 5ggb4929 | Optional Sentry SDK integration (Django + Celery); request/body/cookie scrubbing for auth & payment data, no-op when `SENTRY_DSN` unset |

> Sentry is **off by default** and entirely env-gated by `SENTRY_DSN`. When unset,
> the `sentry-sdk` package is never imported and startup, request handling, and
> Celery are completely unaffected — `sentry-sdk` is an optional dependency.

## Quick Start (local)

```bash
# 1. Enable metrics in .env (required before Prometheus can scrape)
echo "PROMETHEUS_ENABLED=1" >> .env

# 2. Start the default stack (web, RabbitMQ, Flower)
make up-d

# 3. Start the monitoring stack (Prometheus + Grafana)
make up-monitoring

# 4. Open in your browser:
#    App:        http://localhost:8089/
#    Metrics:    http://localhost:8089/metrics  (or via Prometheus)
#    Prometheus: http://localhost:9090/
#    Grafana:    http://localhost:3000/  (admin/admin)
```

Stop the monitoring stack (keeps volumes/data):
```bash
make monitoring-down
```

## Architecture

```
┌──────────────────┐         ┌──────────────┐          ┌──────────────┐
│ Django web/app   │  scrape │ Prometheus   │  query   │   Grafana    │
│ (port 8089)      │  ─────► │ (port 9090)  │ ───────► │  (port 3000) │
│ /metrics         │         │              │          │              │
│ /health          │         └──────────────┘          └──────────────┘
│                  │                  ▲                       │
│ Celery workers   │                  │                       │
│ (instrumented)   │                  └───────────────────────┘
│                  │              Prometheus TSDB (30d retention)
└──────────────────┘
```

### What gets scraped

Prometheus scrapes `http://web:8000/metrics` every 15 seconds. The Django
app exposes these metrics (all with bounded labels — see card g37aggbd):

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `django_http_requests_total` | Counter | `method`, `status` | HTTP requests by method (GET/POST/…) and status group (2xx/3xx/4xx/5xx) |
| `django_http_request_duration_seconds` | Histogram | `method`, `status` | HTTP request latency |
| `django_orders_total` | Counter | `status` | Orders created by Order.Status (PENDING/PAID/…) |
| `django_orders_revenue_total` | Counter | `status` | Gross order revenue by status ($) |
| `django_orders_refunded_total` | Counter | `order_id` | Refunded amount per order |
| `django_celery_tasks_total` | Counter | `task`, `status` | Celery task completions by name + success/failure |
| `django_celery_task_duration_seconds` | Histogram | `task` | Celery task execution time |

### Health check

The `/health/` endpoint (always available, no auth required) returns:

```json
{"status": "ok", "timestamp": "2026-01-01T00:00:00Z", "database": "ok"}
```

Use this for load-balancer liveness/readiness probes:
```bash
curl http://localhost:8089/health/
```

## Grafana Dashboards

Three dashboards are auto-provisioned from `docker/grafana/dashboards/`:

### 1. HTTP Health, Latency & Errors (`django-ecommerce-http`)
- HTTP request rate (RPS)
- Error rates by status group (4xx, 5xx)
- P95 HTTP latency
- Request volume by status group (timeseries)

### 2. Orders, Payments & Refunds (`django-ecommerce-business`)
- Order creation rate
- Orders by status (pie chart)
- Total cumulative revenue
- Revenue rate by status
- Refunded amount by order (bar gauge)

### 3. Celery Task Health & Latency (`django-ecommerce-celery`)
- Task throughput (tasks/s)
- Failure rate
- Success rate (%)
- Task throughput by task name and status
- P95 task duration by task name

## Protecting /metrics

The `/metrics` endpoint is gated by `PROMETHEUS_ENABLED` (returns 404 when off)
and optionally restricted by IP via `PROMETHEUS_METRICS_ALLOWED_IPS` in `.env`:

```bash
# Only allow Prometheus (running on the Docker network) to scrape
PROMETHEUS_METRICS_ALLOWED_IPS=172.20.0.0/16

# Or localhost only (for local testing)
PROMETHEUS_METRICS_ALLOWED_IPS=127.0.0.1
```

In production, also restrict access at the reverse proxy (nginx) level.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PROMETHEUS_ENABLED` | (unset = off) | Enable `/metrics` endpoint (set to `1`) |
| `PROMETHEUS_METRICS_ALLOWED_IPS` | (empty = open) | Comma-separated IP allowlist for `/metrics` |
| `PROMETHEUS_PORT` | `9090` | Host port for Prometheus UI |
| `GRAFANA_PORT` | `3000` | Host port for Grafana UI |
| `GRAFANA_ADMIN_USER` | `admin` | Grafana admin username |
| `GRAFANA_ADMIN_PASSWORD` | `admin` | Grafana admin password (**change in prod**) |
| `PROMETHEUS_URL` | `http://prometheus:9090` | URL Grafana uses for Prometheus queries |
| `GRAFANA_ENVIRONMENT` | `dev` | Label in dashboard variables |
| `SENTRY_DSN` | (unset = off) | Enable Sentry error tracking (paste DSN from your Sentry project) |
| `SENTRY_ENVIRONMENT` | `unknown` | Environment tag (e.g. `production`, `staging`) |
| `SENTRY_RELEASE` | auto | Release tag; derived from `GIT_COMMIT`/`APP_VERSION` or set explicitly |
| `SENTRY_TRACES_SAMPLE_RATE` | `0.2` | Transaction sampling rate |
| `SENTRY_PROFILES_SAMPLE_RATE` | `0.0` | Profiling sampling rate (off by default) |
| `SENTRY_SEND_DEFAULT_PII` | `false` | Capture Django user/PII automatically (off by default) |
| `SENTRY_SCRUB_REQUEST_HEADERS` | `true` | Scrub auth/cookie headers from request data |
| `SENTRY_SCRUB_REQUEST_COOKIES` | `true` | Redact sessionid/csrftoken cookie values |
| `SENTRY_SCRUB_PAYMENT_DATA` | `true` | Redact card/CVC/Stripe-key field values |
| `SENTRY_SENSITIVE_FIELDS` | (built-ins) | Extra comma-separated field suffixes to scrub |
| `SENTRY_CELERY_ENABLED` | `true` | Attach CeleryIntegration |
| `SENTRY_DJANGO_ENABLED` | `true` | Attach DjangoIntegration |

## Sentry Error Tracking

Sentry captures unhandled exceptions and performance traces across the Django
app, background Celery tasks, and management commands. It is **off by default**
and activated by setting `SENTRY_DSN` in `.env` (or the environment):

```bash
# Enable — paste the DSN from your Sentry project settings
SENTRY_DSN=https://examplePublicKey@o0.ingest.sentry.io/123456789

# Tag your environment / release for filtering in Sentry
SENTRY_ENVIRONMENT=production
SENTRY_RELEASE=v1.2.3   # optional; auto-derived from GIT_COMMIT/APP_VERSION
```

The DSN is passed to the `web` and `celery-worker` Compose services
automatically (see `docker-compose.yml`); no code change is required to enable
it in a running stack — set the var and restart.

### What gets reported

| Source | Integration | What's captured |
|--------|-------------|-----------------|
| Django web requests | `DjangoIntegration` | unhandled view exceptions, 500s, slow requests |
| Celery tasks | `CeleryIntegration` | task failures and exceptions |
| Management commands | (auto) | unhandled exceptions |

Transactions (performance traces) are sampled at `SENTRY_TRACES_SAMPLE_RATE`
(default `0.2` = 20% of requests). Set `SENTRY_PROFILES_SAMPLE_RATE` to a
non-zero value to enable profiling sampling.

### Sensitive-data scrubbing

Sentry is wired to **never leak** auth tokens, session cookies, or payment data
(card 5ggb4929). The following protections are on by default when `SENTRY_DSN`
is set and can be toggled per-environment:

| Behavior | Default | Env var |
|----------|---------|---------|
| Strip `Authorization`, `Cookie`, `Set-Cookie`, `X-CSRFToken` headers from captured request data | On | `SENTRY_SCRUB_REQUEST_HEADERS` |
| Redact `sessionid` / `csrftoken` / token cookie **values** | On | `SENTRY_SCRUB_REQUEST_COOKIES` |
| Redact payment/credential field values (card number, CVC, expiry, Stripe secret keys, passwords, tokens) | On | `SENTRY_SCRUB_PAYMENT_DATA` |
| Capture Django user / PII automatically | Off | `SENTRY_SEND_DEFAULT_PII` |

A custom `before_send` processor (`SentryScrubbingProcessor`) walks the entire
event payload — `request.data`, `extra`, `user`, `contexts` — and replaces any
sensitive field's value with `***REDACTED***` (matching the same key suffix
policy the project uses for structured-log redaction — see
`ecommerce/logging.py` `SENSITIVE_SUFFIXES`). Safe Stripe correlation tokens
(`idempotency_key`, `client_reference_id`, `order_id`) are explicitly **kept**
so refund/replay tracing still works in Sentry.

**Fail-safe invariant:** even without a `SentryConfig` (e.g. when the processor
is constructed standalone), the scrubber always redacts sensitive field values
by default. The `SENTRY_SCRUB_PAYMENT_DATA` and `SENTRY_SCRUB_REQUEST_COOKIES`
flags are respected only when explicitly wired through `SentryConfig`; setting
either to `false` opts out of that specific scrubbing layer. Safe Stripe
correlation tokens are always preserved regardless of these flags.

To add project-specific fields to the scrub set:

```bash
SENTRY_SENSITIVE_FIELDS=luhn_token,internal_api_secret
```

### Off by default in tests

The Django test suite never touches the network for Sentry: `init_sentry()` is a
no-op when `SENTRY_DSN` is unset, and the SDK is imported lazily only when a DSN
is present. The Sentry unit tests mock `sentry_sdk.init` and exercise the
scrubbing processor on synthetic event payloads.

## File Layout

```
docker/
├── prometheus/
│   └── prometheus.yml           # Scrape config (targets web:8000/metrics)
├── grafana/
│   ├── dashboards/              # Grafana dashboard JSON files
│   │   ├── http-health-latency-errors.json
│   │   ├── orders-payments-refunds.json
│   │   └── celery-task-health-latency.json
│   ├── provisioning/
│   │   ├── datasources/
│   │   │   └── datasource.yml   # Prometheus datasource (auto-provisioned)
│   │   └── dashboards/
│   │       └── dashboard-provider.yml  # Dashboard folder auto-provisioning
│   └── scripts/
│       └── validate_dashboards.py  # Standalone JSON/provisioning validator
```

## Validation

### Standalone validator (no Docker/Django needed)

```bash
python3 docker/grafana/scripts/validate_dashboards.py
# or
make grafana-dashboards-validate
```

This validates:
- All dashboard JSON files are syntactically valid
- Each dashboard has required top-level keys (annotations, panels, etc.)
- Each dashboard has a unique UID
- Every panel target has a non-empty PromQL expression
- Every panel references at least one app-defined metric
- Every panel uses the Prometheus datasource
- The Prometheus config references `web:8000` and `/metrics`
- The Grafana datasource and dashboard provisioning YAML files have the expected structure

### Django test suite

The same validation runs as part of the Django test suite:
```bash
make test  # includes ecommerce.metrics.tests.test_grafana_provisioning
```

14 tests covering dashboard structure, metric references, provisioning YAML, and
dashboard coverage (HTTP, business, Celery).
