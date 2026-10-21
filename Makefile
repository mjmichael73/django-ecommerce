# Django ecommerce — Docker-first development (run from repo root)
.PHONY: help build up up-d up-detached up-proxy up-d-proxy up-d-flower down logs logs-web ps \
	restart-web restart-worker migrate makemigrations seed createsuperuser shell \
	collectstatic verify-pdf-static test manage sh check-deploy flower down-volumes clean \
	up-monitoring monitoring-down grafana-dashboards-validate \
	backup backup-restore backup-redis backup-dry-run

COMPOSE ?= docker compose
WEB := web

# Compose interpolates ${SECRET_KEY:?…} for every command. For targets that do not start the
# app (`build`, `down`, logs, `ps`, `restart`, etc.), allow a parse-only placeholder when
# SECRET_KEY is unset. Never use this for `up` — create `.env` first (`cp .env.example .env`).
COMPOSE_PARSE_ONLY_ENV = SECRET_KEY="$${SECRET_KEY:-__compose_parse_only_run_make_env_before_up__}"

help: ## Show targets (all commands assume Docker Compose is running unless noted)
	@echo "Docker development — http://localhost:8089 · RabbitMQ UI http://localhost:15672"
	@echo "Flower: http://localhost:5555 (starts with make up-d) · Optional nginx :8080 (make up-d-proxy)"
	@echo ""
	@echo "Detached stack:  make up-d   (hyphen in target name — NOT \"make up -d\", that is Make debug)"
	@echo ""
	@echo "First time:   cp .env.example .env   (fill SECRET_KEY + POSTGRES_PASSWORD), then:  make build && make up-d"
	@echo "Then:         make migrate && make seed   (demo data + superadmin/password)"
	@echo ""
	@echo "Usage: make [target]"
	@echo ""
	@grep -hE '^[a-zA-Z0-9_.-]+:.*##' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*## "}; {printf "  %-20s %s\n", $$1, $$2}' | sort

build: ## Build Compose images
	@$(COMPOSE_PARSE_ONLY_ENV) $(COMPOSE) build

up: ## Start full stack in the foreground (logs attached)
	$(COMPOSE) up

up-d: ## Start full stack detached (background)
	$(COMPOSE) up -d

up-detached: up-d ## Alias for up-d (detached — use this spelling; not “make up -d”)

up-proxy: ## Start stack with nginx reverse-proxy (foreground) — app on :8080 and :8000
	$(COMPOSE) --profile reverse-proxy up

up-d-proxy: ## Same with reverse-proxy detached
	$(COMPOSE) --profile reverse-proxy up -d

up-d-flower: up-d ## Legacy alias — Flower is included in `make up-d` now

up-monitoring: ## Start the optional Prometheus+Grafana monitoring stack (profile: monitoring)
	$(COMPOSE) --profile monitoring up -d prometheus grafana

monitoring-down: ## Stop the optional monitoring stack (keeps prometheus/grafana volumes)
	@$(COMPOSE_PARSE_ONLY_ENV) $(COMPOSE) --profile monitoring down

grafana-dashboards-validate: ## Validate Grafana dashboard JSON structure (standalone script)
	@python3 docker/grafana/scripts/validate_dashboards.py

backup-dry-run: ## Backup: dry-run (print planned actions, write nothing)
	@$(COMPOSE_PARSE_ONLY_ENV) $(COMPOSE) --profile backup run --rm \
		--entrypoint sh \
		-e BACKUP_KEEP_N="$(BACKUP_KEEP_N)" \
		backup /backup/backup.sh --dry-run

backup: ## Backup: one-shot DB + media dump to ./backups/ (profile: backup)
	@$(COMPOSE_PARSE_ONLY_ENV) $(COMPOSE) --profile backup run --rm \
		-e BACKUP_KEEP_N="$(BACKUP_KEEP_N)" \
		backup

backup-restore: ## Backup: restore/verify a dump (ARGS="backups/<file>.dump"); use CONFIRM=scratch to restore into the scratch db
	@if [ -z "$(ARGS)" ]; then \
		echo "usage: make backup-restore ARGS=backups/db-<ts>.dump [CONFIRM=scratch]"; \
		exit 2; \
	fi
	@$(COMPOSE_PARSE_ONLY_ENV) $(COMPOSE) --profile backup run --rm \
		--entrypoint sh \
		-e CONFIRM="$(CONFIRM)" \
		-e KEEP_SCRATCH="$(KEEP_SCRATCH)" \
		backup /backup/restore.sh "$(ARGS)"

backup-redis: ## Backup: optional Redis RDB dump (requires BACKUP_REDIS=1)
	@if [ -z "$(BACKUP_REDIS)" ]; then \
		echo "set BACKUP_REDIS=1 to enable Redis backups"; \
		exit 1; \
	fi
	@$(COMPOSE_PARSE_ONLY_ENV) $(COMPOSE) --profile backup run --rm \
		-e BACKUP_REDIS="$(BACKUP_REDIS)" \
		redis-backup

down: ## Stop stack (keeps volumes / database data)
	@$(COMPOSE_PARSE_ONLY_ENV) $(COMPOSE) down

down-volumes: ## Stop stack and remove volumes (media; Postgres+Redis data live in shared instance)
	@$(COMPOSE_PARSE_ONLY_ENV) $(COMPOSE) down -v

logs: ## Follow logs for all services
	@$(COMPOSE_PARSE_ONLY_ENV) $(COMPOSE) logs -f

logs-web: ## Follow logs for the web service only
	@$(COMPOSE_PARSE_ONLY_ENV) $(COMPOSE) logs -f $(WEB)

ps: ## List Compose services
	@$(COMPOSE_PARSE_ONLY_ENV) $(COMPOSE) ps

restart-web: ## Restart the web container
	@$(COMPOSE_PARSE_ONLY_ENV) $(COMPOSE) restart $(WEB)

restart-worker: ## Restart the Celery worker
	@$(COMPOSE_PARSE_ONLY_ENV) $(COMPOSE) restart celery-worker

migrate: ## Run migrations (requires stack up: make up-d)
	@$(COMPOSE_PARSE_ONLY_ENV) $(COMPOSE) exec $(WEB) python manage.py migrate

makemigrations: ## Create migration files inside the web container
	@$(COMPOSE_PARSE_ONLY_ENV) $(COMPOSE) exec $(WEB) python manage.py makemigrations

seed: ## Demo categories, products (JPEG placeholders), superuser superadmin / password
	@$(COMPOSE_PARSE_ONLY_ENV) $(COMPOSE) exec $(WEB) python manage.py seed

createsuperuser: ## Interactive createsuperuser
	@$(COMPOSE_PARSE_ONLY_ENV) $(COMPOSE) exec -it $(WEB) python manage.py createsuperuser

shell: ## Django shell (interactive)
	@$(COMPOSE_PARSE_ONLY_ENV) $(COMPOSE) exec -it $(WEB) python manage.py shell

sh: ## Shell inside the web container (/bin/sh)
	@$(COMPOSE_PARSE_ONLY_ENV) $(COMPOSE) exec -it $(WEB) sh

collectstatic: ## collectstatic (entrypoint usually runs this on start)
	@$(COMPOSE_PARSE_ONLY_ENV) $(COMPOSE) exec $(WEB) python manage.py collectstatic --noinput

verify-pdf-static: ## Host check: collectstatic + assert static/css/pdf.css (CI parity, needs venv)
	cd be/ecommerce && SECRET_KEY="$${SECRET_KEY:-__local_collectstatic_check__}" python3 manage.py collectstatic --noinput && test -f static/css/pdf.css && echo OK

 # Tests must not read or mutate the shared Redis cache (DB 2): an empty
 # REDIS_CACHE_URL keeps the LocMem cache, so every run starts from scratch.
test: ## Run Django tests via compose run (bind-mounts ./be/ecommerce; DB etc. must be up: make up-d)
	@$(COMPOSE_PARSE_ONLY_ENV) $(COMPOSE) run --rm \
		-e SKIP_COLLECTSTATIC=1 \
		-e REDIS_CACHE_URL= \
		-e ECOMMERCE_DOCKER_DIR=/mnt/docker \
		-v "$(CURDIR)/be/ecommerce:/app" \
		-v "$(CURDIR)/docker:/mnt/docker:ro" \
		-v "$(CURDIR):/repo:ro" \
		-v media_data:/app/media \
		$(WEB) python manage.py test ecommerce.tests ecommerce.tests.sentry ecommerce.metrics.tests orders.tests payment.tests coupons.tests accounts.tests shop.tests returns.tests wishlist.tests -v2

manage: ## Run any manage.py command, e.g. make manage ARGS='showmigrations'
	@$(COMPOSE_PARSE_ONLY_ENV) $(COMPOSE) exec $(WEB) python manage.py $(ARGS)

check-deploy: ## Django deployment checks (use DEBUG=False and real ALLOWED_HOSTS in .env)
	@$(COMPOSE_PARSE_ONLY_ENV) $(COMPOSE) exec $(WEB) python manage.py check --deploy

flower: ## Ensure Flower is running (starts stack detached if needed)
	@$(COMPOSE) up -d flower

clean: ## Remove __pycache__ on the host under be/ecommerce (not inside containers)
	find be/ecommerce -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true

.DEFAULT_GOAL := help
