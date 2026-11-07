"""Lưu lịch sử từng lượt làm sạch độc lập với ingestion job và snapshot Silver."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sqlalchemy import insert, update

from app.modules.pipeline.models import CLEANING_RUNS

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection, Engine


class CleaningRunRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def start(self, context: dict[str, Any], *, entity: str, actor: str, session: str) -> str:
        run_id = str(uuid4())
        # Ghi lượt chạy trước khi đọc file Bronze để tra được cả các lượt xử lý lỗi.
        with self.engine.begin() as conn:
            conn.execute(
                insert(CLEANING_RUNS).values(
                    run_id=run_id,
                    ingestion_job_id=context["ingestion_job_id"],
                    dataset_id=context["dataset_id"],
                    domain=context["domain"],
                    entity=entity,
                    requested_by=actor[:100],
                    session=session,
                    bronze_version=context["output"]["version_id"],
                    cleaning_status="RUNNING",
                    publication_status="NOT_STARTED",
                    records_published=0,
                    started_at=datetime.now(UTC),
                )
            )
        return run_id

    def update(self, run_id: str, *, connection: Connection | None = None, **values: Any) -> None:
        statement = update(CLEANING_RUNS).where(CLEANING_RUNS.c.run_id == run_id).values(**values)
        if connection is not None:
            connection.execute(statement)
        else:
            with self.engine.begin() as conn:
                conn.execute(statement)
