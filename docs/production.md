# Production Deployment — Django E-commerce

## Overview

This guide covers deploying Django E-commerce to production using Docker Compose.

## Prerequisites

- Docker & Docker Compose
- Domain name with DNS configured
- SSL/TLS certificate (or use Let's Encrypt)
- Stripe account for payments

## Environment Variables

Create `.env.production`:

```bash
# Django
SECRET_KEY=<generate-strong-key>
DEBUG=false
ALLOWED_HOSTS=yourdomain.com
CSRF_TRUSTED_ORIGINS=https://yourdomain.com

# Database
POSTGRES_DB=ecommerce
POSTGRES_USER=<secure-username>
POSTGRES_PASSWORD=<secure-password>

# Redis
REDIS_URL=redis://redis:6379/0

# Celery
CELERY_BROKER_URL=amqp://rabbitmq:5672
CELERY_RESULT_BACKEND=redis://redis:6379/1

# Stripe
STRIPE_SECRET_KEY=sk_live_...
STRIPE_PUBLISHABLE_KEY=pk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...

# Email (optional)
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_HOST_USER=your-email@gmail.com
EMAIL_HOST_PASSWORD=your-app-password
```

## Docker Compose Deployment

### 1. Build Images

```bash
# Build application image
docker build -t django-ecommerce .

# Or use docker compose
docker compose build
```

### 2. Start Services

```bash
# Start all services
docker compose -f docker-compose.prod.yml up -d

# Check status
docker compose ps

# View logs
docker compose logs -f web
```

### 3. Initialize Database

```bash
# Run migrations
docker compose exec web python manage.py migrate

# Create superuser
docker compose exec web python manage.py createsuperuser

# Collect static files
docker compose exec web python manage.py collectstatic --noinput
```

### 4. Configure Nginx

Create `nginx/nginx.conf`:

```nginx
upstream web {
    server web:8000;
}

server {
    listen 80;
    server_name yourdomain.com;
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name yourdomain.com;

    ssl_certificate /etc/nginx/ssl/fullchain.pem;
    ssl_certificate_key /etc/nginx/ssl/privkey.pem;

    # Security headers
    add_header X-Frame-Options "DENY" always;
    add_header X-Content-Type-Options "nosniff" always;

    # Static files
    location /static/ {
        alias /app/staticfiles/;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    # Media files
    location /media/ {
        alias /app/media/;
        expires 7d;
    }

    # Application
    location / {
        proxy_pass http://web;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## Production Checklist

Use this before pointing a real domain or live Stripe keys at the app:

| Area | Check |
|------|-------|
| **Secrets** | `SECRET_KEY` is long, random, unique per environment; **never** committed (`.env` is gitignored). Rotate if it ever leaked. |
| **Django** | `DEBUG=False`. `ALLOWED_HOSTS` lists only your domain(s) (never `*`). Set `CSRF_TRUSTED_ORIGINS` for each HTTPS origin. Use `DJANGO_BEHIND_PROXY=1` behind nginx/Traefik; run `make check-deploy`. |
| **Database** | `POSTGRES_PASSWORD` is strong and not the sample `ecommerce` value. Restrict Postgres to internal networks only. |
| **Stripe** | Live `STRIPE_*` keys and `STRIPE_WEBHOOK_SECRET`; webhook URL uses HTTPS and matches the deployed host. |
| **Email** | Real `EMAIL_*` / SMTP or provider backend — not the console backend. |
| **TLS** | Terminate TLS at nginx (this repo includes a proxy profile), Traefik, or your LB; do not expose Gunicorn publicly. Prefer redirects to HTTPS at the proxy; see `SECURE_SSL_REDIRECT` in `.env.example`. |
| **Broker / cache** | RabbitMQ is not exposed publicly. Redis is the **shared instance** from `~/projects/shared-services`; its persistence and exposure are managed there. |
| **Media & static** | Media volume or object storage is backed up; understand who can read/write `MEDIA_ROOT`. |
| **Process** | Run `python manage.py check --deploy` in the target environment and fix reported issues. |
| **Dependencies** | Pin or lock images and Python packages; plan security updates. |

## Health Checks

```bash
# Application health
curl https://yourdomain.com/health/

# Database health
docker compose exec postgres pg_isready

# Redis (shared instance — check via plain docker exec; not part of this Compose project)
docker exec redis redis-cli ping
```

## Monitoring

### Prometheus

The app exposes a `/metrics` endpoint (Prometheus text exposition) and a
`/health/` endpoint (JSON DB probe) for load-balancer liveness/readiness
checks.

**Enabling metrics** — set `PROMETHEUS_ENABLED=1` in your `.env` (default is
off; the app starts normally without any metrics overhead).

**Protecting `/metrics`** — set `PROMETHEUS_METRICS_ALLOWED_IPS` to a
comma-separated list of source IPs that may scrape (e.g. the Prometheus server's
Docker-network IP, or `127.0.0.1` for local testing). Requests from non-allowlisted
IPs get `403`. Leave empty for open access behind a restricted reverse proxy.

```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'django-ecommerce'
    static_configs:
      - targets: ['web:8000']
    metrics_path: '/metrics'
```

**Available metrics:**

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `django_http_requests_total` | Counter | `method`, `status` | HTTP requests by method (GET/POST/…) and status group (2xx/3xx/4xx/5xx) |
| `django_http_request_duration_seconds` | Histogram | `method`, `status` | HTTP request latency |
| `django_orders_total` | Counter | `status` | Orders created by Order.Status (PENDING/PAID/…) |
| `django_orders_revenue_total` | Counter | `status` | Gross order revenue by status ($) |
| `django_orders_refunded_total` | Counter | `order_id` | Refunded amount per order |
| `django_celery_tasks_total` | Counter | `task`, `status` | Celery task completions by name + success/failure |
| `django_celery_task_duration_seconds` | Histogram | `task` | Celery task execution time |

**Health check:**

```bash
curl https://yourdomain.com/health/
# {"status":"ok","timestamp":"2026-01-01T00:00:00Z","database":"ok"}
```

### Grafana

Access Grafana at `http://localhost:3000` (default credentials: admin/admin)

## Backup & Restore

### Database Backup

```bash
# Backup
docker compose exec postgres pg_dump -U postgres ecommerce > backup.sql

# Restore
docker compose exec -T postgres psql -U postgres ecommerce < backup.sql
```

### Automated Backups

```bash
# Add to crontab
0 2 * * * docker compose exec postgres pg_dump -U postgres ecommerce | gzip > /backups/ecommerce-$(date +\%Y\%m\%d).sql.gz
```

## Rollback

```bash
# Docker Compose
docker compose down
docker compose -f docker-compose.prev.yml up -d
```

## Troubleshooting

See TROUBLESHOOTING.md for common issues.