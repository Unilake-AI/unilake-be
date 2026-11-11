.DEFAULT_GOAL := dev

PYTHON ?= .venv/bin/python
HOST ?= 0.0.0.0
PORT ?= 8000

.PHONY: dev run worker test lint db-upgrade db-current db-migrate

# Chỉ API chạy trong Terminal; các thành phần còn lại tiếp tục chạy bằng Docker.
dev:
	@$(PYTHON) -c "import uvicorn"
	docker compose up -d postgres minio minio-init ingestion-worker
	docker compose stop backend
	$(PYTHON) -m uvicorn app.main:app --host $(HOST) --port $(PORT) --reload --reload-dir app

run: dev

worker:
	python -m app.ingestion_worker
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
