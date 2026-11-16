.DEFAULT_GOAL := dev

PYTHON ?= .venv/bin/python
HOST ?= 0.0.0.0
PORT ?= 8000

.PHONY: dev run worker silver-worker gold-worker test lint db-upgrade db-current db-migrate db-auto-upgrade db-gold-upgrade

# Chỉ API chạy trong Terminal; các thành phần còn lại tiếp tục chạy bằng Docker.
dev:
	@$(PYTHON) -c "import uvicorn"
	docker compose up -d postgres minio minio-init ingestion-worker silver-worker gold-worker
	docker compose stop backend
	$(PYTHON) -m uvicorn app.main:app --host $(HOST) --port $(PORT) --reload --reload-dir app

run: dev

worker:
	python -m app.ingestion_worker

silver-worker:
	$(PYTHON) -m app.silver_worker

gold-worker:
	$(PYTHON) -m app.gold_worker

db-gold-upgrade:
	docker compose exec -T postgres sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -v ON_ERROR_STOP=1 -f -' < infra/postgres/migrations/20261116_gold_pipeline.sql

db-auto-upgrade:
	docker compose exec -T postgres sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -v ON_ERROR_STOP=1 -f -' < infra/postgres/migrations/20261115_auto_silver_pipeline.sql
test:
	pytest
lint:
	ruff check app tests

db-upgrade:
	alembic upgrade head
db-current:
	alembic current
db-migrate:
	alembic revision --autogenerate -m "$(MSG)"
