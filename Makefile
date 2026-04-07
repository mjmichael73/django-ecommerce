# Django ecommerce — Docker-first development (run from repo root)
.PHONY: help env build up up-d up-detached up-proxy up-d-proxy up-d-flower down logs logs-web ps \
	restart-web restart-worker migrate makemigrations seed createsuperuser shell \
	collectstatic test manage sh check-deploy flower down-volumes clean

COMPOSE ?= docker compose
WEB := web

# Compose interpolates ${SECRET_KEY:?…} for every command. For targets that do not start the
# app (`build`, `down`, logs, `ps`, `restart`, etc.), allow a parse-only placeholder when
# SECRET_KEY is unset. Never use this for `up` — run `make env` before `make up-d`.
COMPOSE_PARSE_ONLY_ENV = SECRET_KEY="$${SECRET_KEY:-__compose_parse_only_run_make_env_before_up__}"

help: ## Show targets (all commands assume Docker Compose is running unless noted)
	@echo "Docker development — http://localhost:8000 · RabbitMQ UI http://localhost:15672"
	@echo "Optional: nginx :8080 (make up-d-proxy) · Flower :5555 (make up-d-flower)"
	@echo ""
	@echo "Detached stack:  make up-d   (hyphen in target name — NOT \"make up -d\", that is Make debug)"
	@echo ""
	@echo "First time:   make env && make build && make up-d"
	@echo "Then:         make migrate && make seed   (demo data + superadmin/password)"
	@echo ""
	@echo "Usage: make [target]"
	@echo ""
	@grep -hE '^[a-zA-Z0-9_.-]+:.*##' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*## "}; {printf "  %-20s %s\n", $$1, $$2}' | sort

env: ## Create .env from .env.example and ensure SECRET_KEY (required by Compose)
	python3 scripts/bootstrap_env.py

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

up-d-flower: ## Detached stack + Flower dashboard at http://localhost:5555
	$(COMPOSE) --profile flower up -d

down: ## Stop stack (keeps volumes / database data)
	@$(COMPOSE_PARSE_ONLY_ENV) $(COMPOSE) down

down-volumes: ## Stop stack and remove volumes (Postgres, media, Redis AOF data)
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

test: ## Run Django tests inside the web container
	@$(COMPOSE_PARSE_ONLY_ENV) $(COMPOSE) exec $(WEB) python manage.py test

manage: ## Run any manage.py command, e.g. make manage ARGS='showmigrations'
	@$(COMPOSE_PARSE_ONLY_ENV) $(COMPOSE) exec $(WEB) python manage.py $(ARGS)

check-deploy: ## Django deployment checks (use DEBUG=False and real ALLOWED_HOSTS in .env)
	@$(COMPOSE_PARSE_ONLY_ENV) $(COMPOSE) exec $(WEB) python manage.py check --deploy

flower: ## Alias: start stack with Flower (same as up-d-flower)
	@$(MAKE) up-d-flower

clean: ## Remove __pycache__ on the host under be/ecommerce (not inside containers)
	find be/ecommerce -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true

.DEFAULT_GOAL := help
