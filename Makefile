# Django ecommerce — Docker-first development (run from repo root)
.PHONY: help env build up up-d down logs logs-web ps restart-web restart-worker \
	migrate makemigrations seed createsuperuser shell collectstatic test manage sh \
	flower down-volumes clean

COMPOSE ?= docker compose
WEB := web

help: ## Show targets (all commands assume Docker Compose is running unless noted)
	@echo "Docker development — http://localhost:8000 · RabbitMQ UI http://localhost:15672"
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
	$(COMPOSE) build

up: ## Start full stack in the foreground (logs attached)
	$(COMPOSE) up

up-d: ## Start full stack detached (background)
	$(COMPOSE) up -d

down: ## Stop stack (keeps volumes / database data)
	$(COMPOSE) down

down-volumes: ## Stop stack and remove volumes (wipes Postgres and media volume)
	$(COMPOSE) down -v

logs: ## Follow logs for all services
	$(COMPOSE) logs -f

logs-web: ## Follow logs for the web service only
	$(COMPOSE) logs -f $(WEB)

ps: ## List Compose services
	$(COMPOSE) ps

restart-web: ## Restart the web container
	$(COMPOSE) restart $(WEB)

restart-worker: ## Restart the Celery worker
	$(COMPOSE) restart celery-worker

migrate: ## Run migrations (requires stack up: make up-d)
	$(COMPOSE) exec $(WEB) python manage.py migrate

makemigrations: ## Create migration files inside the web container
	$(COMPOSE) exec $(WEB) python manage.py makemigrations

seed: ## Demo categories, products (JPEG placeholders), superuser superadmin / password
	$(COMPOSE) exec $(WEB) python manage.py seed

createsuperuser: ## Interactive createsuperuser
	$(COMPOSE) exec -it $(WEB) python manage.py createsuperuser

shell: ## Django shell (interactive)
	$(COMPOSE) exec -it $(WEB) python manage.py shell

sh: ## Shell inside the web container (/bin/sh)
	$(COMPOSE) exec -it $(WEB) sh

collectstatic: ## collectstatic (entrypoint usually runs this on start)
	$(COMPOSE) exec $(WEB) python manage.py collectstatic --noinput

test: ## Run Django tests inside the web container
	$(COMPOSE) exec $(WEB) python manage.py test

manage: ## Run any manage.py command, e.g. make manage ARGS='showmigrations'
	$(COMPOSE) exec $(WEB) python manage.py $(ARGS)

flower: ## Celery Flower at http://localhost:5555
	$(COMPOSE) run --rm -p 5555:5555 celery-worker \
		celery -A ecommerce flower --address=0.0.0.0 --port=5555

clean: ## Remove __pycache__ on the host under be/ecommerce (not inside containers)
	find be/ecommerce -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true

.DEFAULT_GOAL := help
