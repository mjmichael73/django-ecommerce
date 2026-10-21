# Roadmap — Django E-commerce

## Current Status

Production-quality demo shop with good patterns, comprehensive documentation, and Docker-first development. Ready for deployment with minor enhancements.

## Priority: Medium 🟡

## Phase 1: Security Hardening

### 1.1 Critical Security
- [x] Remove default Postgres credentials
- [x] Add rate limiting on checkout/coupon endpoints
- [x] Implement CSRF token rotation
- [x] Add Content Security Policy headers

### 1.2 Authentication
- [ ] Add user registration/login
- [x] Implement social authentication
- [x] Add email verification
- [x] Implement password reset

### 1.3 Payment Security
- [x] Add Stripe webhook signature verification (implemented — card d9347bch; verified by the signed-webhook tests in `payment/tests/test_stripe_webhook.py`)
- [x] Implement payment idempotency
- [x] Add fraud detection
- [x] Create refund handling

## Phase 2: Features

### 2.1 Product Management
- [x] Add product images upload (card ga4e3hg5; uploaded images served absolute under `/media/`, see `shop/tests/test_product_media.py`)
- [x] Implement product variants (card g99ha567; `shop/tests/test_product_variants.py`)
- [x] Add inventory tracking (card 44ge8hgb; `shop/tests/test_inventory.py`)
- [x] Create product reviews (card bd8f759h; `shop/tests/test_product_reviews.py`)

### 2.2 Search & Discovery
- [x] Add full-text search (PostgreSQL) (card bf247ef9; shop/tests/test_full_text_search.py)
- [x] Implement faceted navigation (card 3g83heaa; `shop/tests/test_facets.py`)
- [ ] Add product recommendations
- [ ] Create wish lists

### 2.3 Order Management
- [x] Add order tracking (card ee76485f; `accounts/tests/test_order_tracking.py`, `ecommerce/tests/test_rate_limit.py`)
- [ ] Implement order history
- [x] Add return/refund flow (card 4fcgcg9e; `returns/` app with `ReturnRequest` model, customer-facing form views, rate limiting, admin approve/reject/refund/cancel actions routing through the existing `refund_order` primitive)
- [x] Create admin dashboard (card d3gh87d2; `orders/tests/test_admin_dashboard.py`)

## Phase 3: Performance

### 3.1 Caching
- [x] Add Redis caching for product listings (card 29b8eadg)
- [x] Implement cache invalidation via signals (card 29b8eadg)
- [x] Add CDN for static files (card 327eeba2; optional, env-gated via `STATIC_CDN_URL`/`STATIC_CDN_MANIFEST`; manifest-versioned assets or WhiteNoise fallback)
- [ ] Optimize database queries

### 3.2 Database
- [ ] Add database indexing
- [x] Implement connection pooling (card a8g83e7c; `ecommerce/db_config.py`, `ecommerce/tests/test_postgres_database.py`)
- [x] Add read replicas (card 79e887g9; `ecommerce/db_config.py:parse_replica_urls`, `ecommerce/db_router.py:PrimaryReplicaRouter`, `ecommerce/tests/test_db_router.py`)
- [ ] Optimize N+1 queries

### 3.3 Frontend
- [ ] Add lazy loading
- [ ] Implement pagination
- [ ] Add AJAX cart updates
- [ ] Optimize images

## Phase 4: Operations

### 4.1 Monitoring
- [x] Add Sentry for error tracking (card 5ggb4929 — optional, env-gated by `SENTRY_DSN`; Django + Celery integrations; sensitive-data scrubbing for auth/payment/cookies)
- [x] Implement structured logging (JSON output, request-ID correlation via `X-Request-Id` header, sensitive-data redaction, Celery task propagation — card 6cc44f8g)
- [x] Add Prometheus metrics (card g37aggbd — bounded HTTP request latency/count/status, order revenue, Celery task counters, `/metrics` (IP-allowlisted) and `/health` endpoints)
- [x] Create Grafana dashboards (card 3c3b2e3a — Prometheus + Grafana optional via `--profile monitoring`, 3 dashboards for HTTP/Celery/business metrics, provisioning + validation)

### 4.2 Deployment
- [x] Add Kubernetes manifests (card a9cea652; k8s/ Kustomize base + dev/prod overlays, no-cluster yaml validator + CI step, Django test module, docs/kubernetes.md)
- [ ] Implement blue-green deployment
- [x] Add database migrations strategy (card 65b22ag7; docs/migrations.md + CI guard)
- [x] Create backup procedures (card 36d3gdd6; docker/backup + docs/backup-restore.md)

### 4.3 Testing
- [x] Add unit tests for all apps
- [x] Create integration tests
- [x] Add performance tests (card e4e4dh4g; `ecommerce/tests/test_performance.py`)
- [x] Implement CI/CD pipeline

## Success Criteria

| Metric | Target | Current |
|--------|--------|---------|
| Test coverage | > 80% | Good |
| Security | Production-ready | Good |
| Performance | < 200ms (p95) | Unknown |
| Features | Full e-commerce | Demo |
| Deployment | Kubernetes | Docker Compose |
