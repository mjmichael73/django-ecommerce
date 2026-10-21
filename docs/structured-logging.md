# Structured Logging — Django E-commerce

Card 6cc44f8g — Phase 4.1 monitoring.

## Overview

The project emits **JSON structured logs** with per-request correlation IDs,
replacing Django's default `key value` text formatter. Every log line is a
single-line JSON object on `stdout` so it can be ingested by any log
collector (Sentry, Datadog, Loki, CloudWatch, etc.).

## Request correlation

Every inbound HTTP request receives a correlation ID:

1. If the request carries an `X-Request-Id` header with a valid value
   (1–128 chars of `[A-Za-z0-9_-]`), that value is reused — this lets
   upstream proxies/load balancers inject their own tracing IDs.
2. Otherwise a fresh `uuid4` hex (32 chars) is generated.

The ID is stored in a `contextvars.ContextVar` and injected into every
log record by the `RequestIDLogFilter`. It is also echoed back on the
`X-Request-Id` response header so clients can correlate their requests.

### Celery task propagation

When a task is dispatched from a request context, the ordering view calls
`propagate_request_id()` which forwards the active ID as a `_request_id`
task kwarg. The Celery task-tracing hook in `ecommerce/celery.py` rebinds
the contextvar in the worker before the task runs, so task log lines are
correlated with the originating HTTP request.

## Configuration

All knobs are environment variables, read once at Django settings import:

| Variable   | Default | Description |
|------------|---------|-------------|
| `LOG_JSON` | `1` when `DEBUG=False`, else `0` | `1` = JSON output, `0` = plain text |
| `LOG_LEVEL` | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL` |

Example `.env`:

```env
LOG_JSON=1
LOG_LEVEL=INFO
```

## Sensitive data redaction

The JSON formatter scrubs sensitive fields from `extra` payloads before
serializing. The following key names are redacted (matched case-insensitively
as a *suffix* of the key):

`password`, `passwd`, `secret`, `token`, `apikey`, `api_key`,
`access_token`, `refresh_token`, `authorization`, `auth`,
`card_cvc`, `card_cvv`, `cvv`, `private_key`, `secret_key`,
`webhook_secret`, `stripe_secret_key`, `stripe_publishable_key`,
`google_client_secret`, `csrfmiddlewaretoken`

The `redact()` helper is also exported from `ecommerce.logging` for view and
task code that needs to log request bodies or external API payloads:

```python
from ecommerce.logging import redact

logger.info("stripe webhook", extra={"payload": redact(request.POST.dict())})
```

## Files

| File | Responsibility |
|------|----------------|
| `ecommerce/logging.py` | `JSONFormatter`, `RequestIDLogFilter`, `redact()`, `build_logging_config()` |
| `ecommerce/middleware/request_id.py` | `RequestIDMiddleware`, `request_id()`, `propagate_request_id()` |
| `ecommerce/celery.py` | Worker logging setup + task ID propagation |
| `ecommerce/settings.py` | `LOGGING` dict, `LOG_JSON`/`LOG_LEVEL` knobs |

## Testing

```bash
make test  # runs all tests including ecommerce.tests.test_structured_logging
```

Focused:

```bash
docker compose run --rm -e SKIP_COLLECTSTATIC=1 -e REDIS_CACHE_URL= \
    -v "$PWD/be/ecommerce:/app" web python manage.py test \
    ecommerce.tests.test_structured_logging -v2
```

The test suite covers:

- JSON formatter field stability and single-line output
- `request_id` injection when bound, omission when unbound
- Extra-field pass-through and sensitive-field redaction
- Exception serialization (no local variables leaked)
- Non-serializable value handling
- `RequestIDLogFilter` injection/absence
- `RequestIDMiddleware` ID generation, valid/invalid header handling,
  context isolation between requests, and exception-path cleanup
- `propagate_request_id()` returning empty dict vs. `{'_request_id': ...}`
- End-to-end header echo through the full middleware stack (catalog, 404,
  client-supplied ID)
- Checkout view dispatching the task with `_request_id` matching the header
- Settings integration (LOGGING dict, filter, formatter, handler)
