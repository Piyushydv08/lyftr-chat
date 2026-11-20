COMPOSE ?= docker compose

.DEFAULT_GOAL := help
.PHONY: help up down logs ps restart test test-backend test-frontend clean dev-backend dev-frontend

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-15s %s\n", $$1, $$2}'

.env:
	@cp .env.example .env
	@echo ">> Created .env from .env.example. Put your real LLM_API_KEYS in .env (then run 'make restart')."

up: .env ## Build and start the stack: backend :8000, frontend :3000
	$(COMPOSE) up -d --build
	@echo ""
	@echo "Frontend: http://localhost:3000"
	@echo "Backend:  http://localhost:8000/health"

down: ## Stop the stack (the SQLite volume is kept)
	$(COMPOSE) down

restart: ## Recreate containers after editing .env
	$(COMPOSE) up -d --force-recreate

logs: ## Follow logs from both services
	$(COMPOSE) logs -f

ps: ## Show container status
	$(COMPOSE) ps

test: test-backend test-frontend ## Run all tests inside Docker

test-backend: ## Backend pytest suite (Docker 'test' stage)
	docker build --target test ./backend

test-frontend: ## Frontend vitest suite (Docker 'test' stage)
	docker build --target test ./frontend

clean: ## Stop the stack AND delete the SQLite/cache volume
	$(COMPOSE) down -v

dev-backend: ## Run the backend without Docker (needs Python 3.11+)
	cd backend && python -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt && \
	set -a && . ../.env && set +a && DATABASE_URL=sqlite:///./data/app.db CACHE_DIR=./data/llm_cache \
	uvicorn app.main:app --reload --port 8000

dev-frontend: ## Run the frontend without Docker (needs Node 20+)
	cd frontend && npm install && VITE_API_BASE_URL=http://localhost:8000 npm run dev
