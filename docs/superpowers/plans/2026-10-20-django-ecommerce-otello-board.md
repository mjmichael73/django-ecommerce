# Django E-commerce Otello Board Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Complete every remaining To Do card on the `django-ecommerce` Otello board, with each feature independently tested, reviewed, committed, and merged into `main`.

**Architecture:** Work proceeds sequentially in dependency order. Each card gets an isolated `card/<short-id>-<slug>` branch and a fresh `vyceai/deepseek-v4.1` implementer; the orchestrator reviews and merges it before dispatching the next card. Docker Compose is the only runtime for project commands, PostgreSQL and Redis come from `~/projects/shared-services`, and the web application is published on host port `8089`.

**Tech Stack:** Django 5, PostgreSQL, Redis, RabbitMQ, Celery, Stripe, WeasyPrint, Docker Compose.

**Spec:** `.superpowers/sdd/django-ecommerce-plan.md`

## Global Constraints

- Use Docker and Docker Compose for every project command.
- Use codegraph for orchestrator and subagent context retrieval.
- Use the shared Redis instance in `~/projects/shared-services`; do not define a local Redis service.
- Publish the web container on host port `8089`.
- Every subagent writes comprehensive tests and updates docs when needed.
- Every subagent runs `codegraph index`, commits with a proper message, and updates Otello.
- Every card is implemented and reviewed on its own branch, then merged to `main` before the next card.
- Use `vyceai/deepseek-v4.1` for implementers and reviewers.

## Review Focus

- Shared-service connectivity and database selection must remain isolated per application.
- Existing behavior must remain compatible while new query, cache, and frontend layers are added.
- Anonymous users, unauthenticated requests, empty datasets, and invalid input must receive intentional behavior.
- External integrations such as Stripe, Sentry, CDN, Prometheus, and storage must remain optional and environment-configured.
- Docker startup, migrations, collectstatic, and the full Django suite must continue to work.

## Sequential Tasks

1. Pagination (`d8haf54c`)
2. N+1 query optimization (`3d8653f6`)
3. Database query optimization (`e78g9b52`)
4. Database indexing (`a275hbca`)
5. Redis caching for product listings (`29b8eadg`)
6. Cache invalidation (`ha4ed2ce`)
7. Image optimization (`86ac598h`)
8. Lazy loading (`3agb8235`)
9. AJAX cart updates (`77ce5899`)
10. Product recommendations (`45ba5cfh`)
11. Unit tests for all apps (`bfcaa66a`)
12. Performance tests (`e4e4dh4g`)
13. Integration tests (`e574da32`)
14. Order tracking (`ee76485f`)
15. Return/refund flow (`4fcgcg9e`)
16. Wish lists (`cc26292g`)
17. Structured logging (`6cc44f8g`)
18. Prometheus metrics (`g37aggbd`)
19. Grafana dashboards (`3c3b2e3a`)
20. Sentry (`5ggb4929`)
21. CDN for static files (`327eeba2`)
22. Database migrations strategy (`65b22ag7`)
23. Read replicas (`79e887g9`)
24. Connection pooling (`a8g83e7c`)
25. Blue-green deployment (`828ff8ea`)
26. Kubernetes manifests (`a9cea652`)
27. Backup procedures (`36d3gdd6`)
28. Admin dashboard (`d3gh87d2`)

Each task must include focused tests, Docker-based verification, relevant documentation, `codegraph index`, a commit, an Otello update to `Done`, an orchestrator task review, and a merge to `main`.
