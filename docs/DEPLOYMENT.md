# Deployment Guide — Django E-commerce

## Overview

This guide covers deploying Django E-commerce to production using Docker Compose or Kubernetes.

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

## Container image from CI

The `docker` job in `.github/workflows/ci.yml` builds the production image (`docker compose build web`) on **every** push and pull request, and — **only on pushes to `main`** — publishes it to the GitHub Container Registry:

| Tag | Meaning |
|-----|---------|
| `ghcr.io/<owner>/<repo>:latest` | Most recent successful build of `main` |
| `ghcr.io/<owner>/<repo>:sha-<short>` | Immutable tag per commit (first 7 characters of the SHA) |

The package is private by default; after the first publish, set its visibility under GitHub → Packages if pulls should work anonymously.

**Run it in compose:** replace the `build:` block of the `web` service in `docker-compose.yml` with an `image:` reference (everything else — env vars, networks, entrypoint behavior — stays the same):

```yaml
services:
  web:
    image: ghcr.io/<owner>/<repo>:latest   # instead of build: { context: ., dockerfile: Dockerfile }
```

```bash
docker compose pull web
docker compose up -d web
```

**Smoke test with plain Docker** (bypass the entrypoint: it waits for Postgres and runs migrations, which a one-off container has no database for):

```bash
docker pull ghcr.io/<owner>/<repo>:latest
docker run --rm \
  -e SECRET_KEY=local-smoke-test-only -e POSTGRES_PASSWORD=local-smoke-test-only \
  --entrypoint python ghcr.io/<owner>/<repo>:latest manage.py check
```

## Kubernetes Deployment

### 1. Create Namespace

```yaml
# namespace.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: ecommerce
```

### 2. Deployment

```yaml
# deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: django-ecommerce
  namespace: ecommerce
spec:
  replicas: 3
  selector:
    matchLabels:
      app: django-ecommerce
  template:
    metadata:
      labels:
        app: django-ecommerce
    spec:
      containers:
      - name: web
        image: django-ecommerce:latest
        ports:
        - containerPort: 8000
        envFrom:
        - secretRef:
            name: ecommerce-secrets
        - configMapRef:
            name: ecommerce-config
```

### 3. Service

```yaml
# service.yaml
apiVersion: v1
kind: Service
metadata:
  name: django-ecommerce
  namespace: ecommerce
spec:
  selector:
    app: django-ecommerce
  ports:
  - port: 80
    targetPort: 8000
  type: ClusterIP
```

### 4. Ingress

```yaml
# ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: django-ecommerce
  namespace: ecommerce
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
spec:
  tls:
  - hosts:
    - yourdomain.com
    secretName: ecommerce-tls
  rules:
  - host: yourdomain.com
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: django-ecommerce
            port:
              number: 80
```

### 5. Apply Manifests

```bash
kubectl apply -f namespace.yaml
kubectl apply -f deployment.yaml
kubectl apply -f service.yaml
kubectl apply -f ingress.yaml
```

## Health Checks

```bash
# Application health
curl https://yourdomain.com/health/

# Database health
docker compose exec postgres pg_isready

# Redis (shared instance — not part of this Compose project)
docker exec redis redis-cli ping
```

## Monitoring

### Prometheus

```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'django-ecommerce'
    static_configs:
      - targets: ['web:8000']
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

# Kubernetes
kubectl rollout undo deployment/django-ecommerce -n ecommerce
```

## Troubleshooting

See TROUBLESHOOTING.md for common issues.
