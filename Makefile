.PHONY: run worker test lint db-upgrade db-current db-migrate
run:
	uvicorn app.main:app --reload --port 8000
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
