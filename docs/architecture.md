# Architecture — Django E-commerce

## Overview

A Django 5 demo shop with session-based cart, checkout, Stripe Checkout payments, coupon discounts, and Celery background jobs for order emails and PDF invoices (WeasyPrint).

**Stack:** Django 5.0, PostgreSQL, Redis, RabbitMQ, Celery 5, Stripe, WeasyPrint, Gunicorn, Docker Compose

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Nginx Reverse Proxy                            │
│                              (Port 80/443)                                  │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    │               │               │
                    ▼               ▼               ▼
┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐
│   Django App     │ │   Static Files   │ │   Media Files    │
│   (Gunicorn)     │ │   (Nginx)        │ │   (Nginx)        │
│                  │ │                  │ │                  │
│  ┌────────────┐  │ │                  │ │                  │
│  │  Catalog   │  │ │                  │ │                  │
│  │  Cart      │  │ │                  │ │                  │
│  │  Orders    │  │ │                  │ │                  │
│  │  Payment   │  │ │                  │ │                  │
│  │  Coupons   │  │ │                  │ │                  │
│  └────────────┘  │ │                  │ │                  │
└────────┬─────────┘ └──────────────────┘ └──────────────────┘
         │
         │
┌────────┴────────────────────────────────────────────────────────────────────┐
│                              Celery Workers                                  │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐           │
│  │  Email Worker    │  │  PDF Worker      │  │  Other Workers   │           │
│  │  (Order emails)  │  │  (Invoices)      │  │                  │           │
│  └──────────────────┘  └──────────────────┘  └──────────────────┘           │
└─────────────────────────────────────────────────────────────────────────────┘
         │               │               │
         ▼               ▼               ▼
┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐
│   PostgreSQL     │ │     Redis        │ │    RabbitMQ      │
│   (Database)     │ │   (Cache +       │ │   (Celery        │
│                  │ │    Results)      │ │    Broker)       │
└──────────────────┘ └──────────────────┘ └──────────────────┘
```

## Django Apps

| App | Responsibility |
|-----|----------------|
| **catalog** | Products, categories, search |
| **cart** | Session-based shopping cart |
| **orders** | Order creation and management |
| **payment** | Stripe Checkout integration |
| **coupons** | Discount codes and promotions |

## Data Model

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Category   │     │   Product   │     │    Order    │
├─────────────┤     ├─────────────┤     ├─────────────┤
│ id (PK)     │     │ id (PK)     │     │ id (PK)     │
│ name        │     │ name        │     │ user_id(FK) │
│ slug        │     │ slug        │     │ status      │
│ description │     │ description │     │ total       │
└─────────────┘     │ price       │     │ coupon_id   │
                    │ category_id │     │ created_at  │
                    │ available   │     └─────────────┘
                    │ created_at  │
                    └─────────────┘
                           │
                           ▼
                    ┌─────────────┐     ┌─────────────┐
                    │    Cart     │     │  CartItem   │
                    ├─────────────┤     ├─────────────┤
                    │ id (PK)     │     │ id (PK)     │
                    │ session_key │     │ cart_id(FK) │
                    │ created_at  │     │ product_id  │
                    └─────────────┘     │ quantity    │
                                        └─────────────┘
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Homepage |
| `/catalog/` | GET | Product listing |
| `/catalog/<slug>/` | GET | Product detail |
| `/cart/` | GET | View cart |
| `/cart/add/<id>/` | POST | Add to cart |
| `/cart/remove/<id>/` | POST | Remove from cart |
| `/checkout/` | GET/POST | Checkout flow |
| `/payment/success/` | GET | Payment success |
| `/payment/webhook/` | POST | Stripe webhook |

## Background Jobs

| Queue | Task | Description |
|-------|------|-------------|
| email | `send_order_email` | Send order confirmation |
| email | `send_invoice_email` | Send PDF invoice |
| pdf | `generate_invoice` | Generate PDF invoice |
| default | Other tasks | General background work |

## Docker Services

| Service | Port | Description |
|---------|------|-------------|
| nginx | 80 | Reverse proxy |
| web | 8000 | Django application |
| celery-worker | — | Background job processor |
| celery-beat | — | Periodic task scheduler |
| flower | 5555 | Celery monitoring |
| postgres | 5432 | Database |
| redis | 6379 (DB 1 for Celery results) | Cache + Celery results — **shared instance** from `~/projects/shared-services` |
| rabbitmq | 5672 | Celery broker |

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Database | PostgreSQL | ACID, full-text search |
| Cache | Redis | Fast, pub/sub for Celery |
| Broker | RabbitMQ | Reliable message delivery |
| Payments | Stripe | Industry standard, easy integration |
| PDF | WeasyPrint | HTML/CSS to PDF |
| Auth | Django sessions | Simple, secure by default |

## Security Considerations

- ✅ SECRET_KEY required via Compose `${SECRET_KEY:?...}`
- ✅ SESSION_COOKIE_SECURE and CSRF_COOKIE_SECURE in production
- ✅ ALLOWED_HOSTS enforced when DEBUG=False
- ✅ Rate limiting on checkout/coupon POSTs (429 + Retry-After)
- ⚠️ Default Postgres credentials in docker-compose.yml
