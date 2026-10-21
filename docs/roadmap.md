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
- [ ] Create admin dashboard

## Phase 3: Performance

### 3.1 Caching
- [ ] Add Redis caching for product listings
- [ ] Implement cache invalidation
- [ ] Add CDN for static files
- [ ] Optimize database queries

### 3.2 Database
- [ ] Add database indexing
- [ ] Implement connection pooling
- [ ] Add read replicas
- [ ] Optimize N+1 queries

### 3.3 Frontend
- [ ] Add lazy loading
- [ ] Implement pagination
- [ ] Add AJAX cart updates
- [ ] Optimize images

## Phase 4: Operations

### 4.1 Monitoring
- [ ] Add Sentry for error tracking
- [x] Implement structured logging (JSON output, request-ID correlation via `X-Request-Id` header, sensitive-data redaction, Celery task propagation — card 6cc44f8g)
- [x] Add Prometheus metrics (card g37aggbd — bounded HTTP request latency/count/status, order revenue, Celery task counters, `/metrics` (IP-allowlisted) and `/health` endpoints)
- [ ] Create Grafana dashboards

### 4.2 Deployment
- [ ] Add Kubernetes manifests
- [ ] Implement blue-green deployment
- [ ] Add database migrations strategy
- [ ] Create backup procedures

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
