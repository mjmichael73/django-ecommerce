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
| CSRF Protection | Django middleware | ✅ Enabled |
| Session Security | Secure cookies | ✅ Configured |
| Password Hashing | PBKDF2 | ✅ Default |

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