.DEFAULT_GOAL := dev

PYTHON ?= .venv/bin/python
HOST ?= 0.0.0.0
PORT ?= 8000

.PHONY: dev run worker silver-worker gold-worker test lint db-upgrade db-current db-migrate db-auto-upgrade db-gold-upgrade

# Chỉ API chạy trong Terminal; các thành phần còn lại tiếp tục chạy bằng Docker.
dev:
	@$(PYTHON) -c "import uvicorn"
	docker compose up -d --wait postgres minio
	$(MAKE) db-upgrade
	docker compose up -d minio-init ingestion-worker silver-worker gold-worker
	docker compose stop backend
	$(PYTHON) -m uvicorn app.main:app --host $(HOST) --port $(PORT) --reload --reload-dir app

run: dev

worker:
	python -m app.ingestion_worker

silver-worker:
	$(PYTHON) -m app.silver_worker

gold-worker:
	$(PYTHON) -m app.gold_worker

.PHONY: db-notifications-upgrade
db-notifications-upgrade: db-upgrade

.PHONY: db-silver-default-upgrade
db-silver-default-upgrade: db-upgrade

db-gold-upgrade: db-upgrade

db-auto-upgrade: db-upgrade
test:
	pytest
lint:
	ruff check app tests

db-upgrade:
	$(PYTHON) -m app.modules.pipeline.services.database_upgrade
db-current:
	docker compose exec -T postgres sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -v ON_ERROR_STOP=1 -c "SELECT migration_name,applied_at FROM app.schema_migration ORDER BY migration_name"'
db-migrate:
	alembic revision --autogenerate -m "$(MSG)"

.PHONY: db-catalog-upgrade
db-catalog-upgrade: db-upgrade
