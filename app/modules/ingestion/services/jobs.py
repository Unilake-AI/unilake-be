"""Quản lý và theo dõi lần nạp dữ liệu theo quyền được cấp."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppException
from app.modules.ingestion.schemas import (
    IngestionAttemptRead,
    IngestionJobRead,
    IngestionJobStatus,
    IngestionStart,
)
from app.modules.ingestion.services.access import ingestion_read_domains
from app.modules.ingestion.services.execution import IngestionExecutionService
from app.repositories.ingestion_repo import IngestionRepository

if TYPE_CHECKING:
    from app.modules.ingestion.models import IngestionJob


async def list_jobs(
    db: AsyncSession,
    user: dict[str, Any],
    data_source_id: int | None = None,
    limit: int = 20,
    offset: int = 0,
    dataset_id: int | None = None,
    status: IngestionJobStatus | None = None,
    mine_only: bool = False,
) -> list[IngestionJobRead]:
    jobs = await IngestionRepository(db).list(
        data_source_id,
        limit,
        offset,
        ingestion_read_domains(user),
        dataset_id=dataset_id,
        status=status,
        requested_by=str(user["sub"]) if mine_only else None,
    )
    return [IngestionJobRead.model_validate(job) for job in jobs]


async def job_stats(db: AsyncSession, user: dict[str, Any]) -> dict[str, int]:
    return await IngestionRepository(db).stats(ingestion_read_domains(user))


async def job_metrics(db: AsyncSession, user: dict[str, Any]) -> dict[str, float | None]:
    return await IngestionRepository(db).metrics(ingestion_read_domains(user))


async def job_attempts(
    job_id: int, db: AsyncSession, user: dict[str, Any]
) -> list[IngestionAttemptRead]:
    jobs = await IngestionRepository(db).attempts(job_id, ingestion_read_domains(user))
    return [IngestionAttemptRead.model_validate(job, from_attributes=True) for job in jobs]


async def get_job(job_id: int, db: AsyncSession, user: dict[str, Any]) -> IngestionJobRead:
    job = await IngestionRepository(db).get(job_id, ingestion_read_domains(user))
    return IngestionJobRead.model_validate(job)


async def start_ingestion(
    request: IngestionStart, db: AsyncSession, user: dict[str, Any]
) -> IngestionJob:
    try:
        return await IngestionExecutionService(IngestionRepository(db)).start(request, user)
    except SQLAlchemyError:
        await db.rollback()
        raise AppException(
            "Không thể lưu job ingestion", code="INGESTION_QUEUE_UNAVAILABLE", status_code=503
        ) from None


async def list_workers(db: AsyncSession) -> list[dict[str, object]]:
    now = datetime.now(UTC).replace(tzinfo=None)
    workers = await IngestionRepository(db).workers()
    return [
        {
            "worker_id": worker.worker_id,
            "status": "HEALTHY"
            if (now - worker.last_heartbeat_at).total_seconds()
            <= settings.worker_health_stale_seconds
            else "OFFLINE",
            "current_job_id": worker.current_job_id,
            "last_heartbeat_at": worker.last_heartbeat_at,
        }
        for worker in workers
    ]


async def retry_job(job_id: int, db: AsyncSession, user: dict[str, Any]) -> IngestionJob:
    try:
        return await IngestionRepository(db).retry(job_id, str(user.get("sub", "")))
    except SQLAlchemyError:
        await db.rollback()
        raise AppException(
            "Không thể tạo lần chạy lại", code="INGESTION_RETRY_UNAVAILABLE", status_code=503
        ) from None
