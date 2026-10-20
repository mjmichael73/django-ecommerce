# Security — Django E-commerce

## Reporting a Vulnerability

If you discover a security vulnerability, please report it responsibly:

1. **Do NOT** open a public GitHub issue
2. Email the maintainers directly
3. Include detailed description and reproduction steps
4. Allow time for a fix before public disclosure

## Security Measures

### Authentication & Authorization

| Feature | Implementation | Status |
|---------|---------------|--------|
| User Authentication | Django sessions | ✅ Implemented |
| CSRF Protection | Django middleware + token rotation | ✅ Enabled |
| Session Security | Secure cookies | ✅ Configured |
| Password Hashing | PBKDF2 | ✅ Default |

### CSRF Protection

`django.middleware.csrf.CsrfViewMiddleware` is enabled and every
state-changing form renders `{% csrf_token %}`. Django 5.0 always masks the
token it renders into the form (the transitional `CSRF_COOKIE_MASKED`
setting was removed in Django 5.0, so masking is unconditional), and the
cookie is sent with `SameSite=Lax`.

The token is **rotated on every identity state change**, so a token captured
before the transition cannot be replayed after it:

| Transition | Rotated | Where |
|------------|---------|-------|
| Login | ✅ | `django.contrib.auth.login()` calls `rotate_token()` — Django's built-in `LoginView` is covered out of the box |
| Registration | ✅ | `accounts.views.register` → `ecommerce.csrf_rotation.login_with_csrf_rotation()` |
| Logout | ✅ | `accounts.views.CsrfRotatingLogoutView` — Django's `LogoutView` flushes the session but does **not** rotate the token |

A **failed** login does not rotate the token, so an attacker cannot invalidate
the token of a victim's login form by submitting wrong credentials.

The `csrftoken` cookie holds the unmasked 32-character secret; the value
rendered into forms is a masked 64-character token derived from it.

### Data Protection

| Feature | Implementation | Status |
|---------|---------------|--------|
| HTTPS | Nginx TLS termination | ✅ Configured |
| Database Encryption | PostgreSQL (at rest) | ⚠️ Optional |
| API Keys | Environment variables | ✅ Implemented |
| Secrets | Django SECRET_KEY | ✅ Required |

### Payment Security

| Feature | Implementation | Status |
|---------|---------------|--------|
| PCI Compliance | Stripe Checkout | ✅ Handled by Stripe |
| Card Data | Never stored | ✅ Compliant |
| Webhook Verification | Stripe signatures | ✅ Implemented |
| Idempotency | Order IDs | ✅ Implemented |

### Content Security Policy

A `Content-Security-Policy` header is enforced (not report-only) on every
response the app emits, by `ecommerce.csp_middleware.CSPMiddleware` (card
f3d54f76). The value is compiled from a single settings constant `CSP_POLICY`:

```
default-src 'self'; base-uri 'self'; form-action 'self';
frame-ancestors 'none'; object-src 'none'; script-src 'self';
style-src 'self' 'unsafe-inline' https://fonts.googleapis.com;
font-src 'self' https://fonts.gstatic.com; img-src 'self'
```

Why each non-`'self'` source is allowed:

| Source | Reason (survey finding) |
|--------|-------------------------|
| `'unsafe-inline'` in `style-src` | Templates legitimately use inline `style=""` attributes (cart detail, payment canceled, admin order detail); these are intentionally not restructured. |
| `https://fonts.googleapis.com` in `style-src` | `shop/templates/shop/base.html` loads the Google Fonts stylesheet from this host (intentional third-party typography used on every storefront page); whitelisting it avoids blocking the demo's typography. |
| `https://fonts.gstatic.com` in `font-src` | `fonts.gstatic.com` serves the binary font files referenced by the Google Fonts stylesheet above. |
| `'unsafe-inline'` NOT in `script-src` | The app owns no inline `<script>` blocks. The single inline handler (`onclick="window.print()"` in the admin order detail) was moved to `shop/static/js/admin-order-print.js`, so `script-src` stays `'self'`. |
| `img-src 'self'` (no `data:`) | No data-URI images were found; product/placeholder images load from static/media (same origin). |

No external domains are required for Stripe: it uses Checkout redirect (no Stripe.js or iframe).

Optional reporting: set `CSP_REPORT_URI` (Compose: web service only) to append
a `report-uri` directive for browser→collector violation reports (see
`.env.example`). The header applies to all responses equally, including 429 /
plain ones — `ecommerce.csp_middleware.CSPMiddleware` is registered outermost
(MIDDLEWARE position 0) so it wraps responses produced by all inner layers,
including SecurityMiddleware `SECURE_SSL_REDIRECT` 301s.

## Security Configuration

### Environment Variables

```bash
# Required for production
SECRET_KEY=<strong-random-key>
DEBUG=false
ALLOWED_HOSTS=yourdomain.com

# Database
DATABASE_URL=postgresql://user:pass@host:5432/dbname

# Stripe
STRIPE_SECRET_KEY=sk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...
```

### Django Settings

```python
# settings/production.py
DEBUG = False
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
X_FRAME_OPTIONS = 'DENY'
SECURE_SSL_REDIRECT = True
```

### Nginx Configuration

```nginx
# Security headers
add_header X-Frame-Options "DENY" always;
add_header X-Content-Type-Options "nosniff" always;
add_header X-XSS-Protection "1; mode=block" always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
add_header Content-Security-Policy "default-src 'self'" always;
```

## Dependency Security

### Regular Updates

```bash
# Check for vulnerable dependencies
pip-audit
safety check

# Update dependencies
pip install --upgrade -r requirements.txt
```

### Security Scanning

```bash
# Bandit (Python security linter)
bandit -r .

# Safety (dependency vulnerabilities)
safety check --full-report
```

## Rate Limiting

```python
# Add to settings
REST_FRAMEWORK = {
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle'
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '100/day',
        'user': '1000/day'
    }
}
```

## Input Validation

- Use Django forms for input validation
- Sanitize user inputs
- Use parameterized queries (Django ORM)
- Validate file uploads

## Logging & Monitoring

```python
# Log security events
LOGGING = {
    'handlers': {
        'security': {
            'class': 'logging.FileHandler',
            'filename': 'security.log',
        },
    },
    'loggers': {
        'django.security': {
            'handlers': ['security'],
            'level': 'INFO',
        },
    },
}
```

## Security Checklist

### Pre-Deployment

- [ ] DEBUG = False
- [ ] SECRET_KEY is strong and unique
- [ ] ALLOWED_HOSTS configured
- [ ] HTTPS enabled
- [ ] Security headers configured
- [x] Rate limiting enabled
- [x] CSRF token rotation on login/registration/logout
- [ ] Input validation implemented
- [ ] Dependencies updated
- [ ] Security scan passed

### Post-Deployment

- [ ] Monitor security logs
- [ ] Review access logs
- [ ] Check for vulnerabilities
- [ ] Update dependencies regularly
- [ ] Rotate secrets periodically

## Contact

For security concerns, contact the maintainers directly.