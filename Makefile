.DEFAULT_GOAL := help
VENV := .venv
BIN := $(VENV)/bin
COMPOSE := docker compose --env-file .env.production

.PHONY: help install dev test lint format check lock docker-build up down logs

help: ## Show this help
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-13s\033[0m %s\n", $$1, $$2}'

install: ## Create .venv with the pinned runtime and the dev tools
	python3 -m venv $(VENV)
	$(BIN)/pip install -r requirements.lock -e ".[dev]"

dev: ## Run the API locally with auto-reload
	$(BIN)/uvicorn app.main:app --reload

test: ## Run the test suite
	$(BIN)/pytest

lint: ## Check style and common bugs
	$(BIN)/ruff check .
	$(BIN)/ruff format --check .

format: ## Fix what ruff can fix and format the code
	$(BIN)/ruff check --fix .
	$(BIN)/ruff format .

check: lint test ## Lint and test: run before every commit

docker-build: ## Build the production image
	docker build -t chatbot-api .

up: ## Start production (API + Caddy), see DEPLOY.md
	$(COMPOSE) up -d --build

down: ## Stop production
	$(COMPOSE) down

logs: ## Follow the API's production logs
	$(COMPOSE) logs -f api
