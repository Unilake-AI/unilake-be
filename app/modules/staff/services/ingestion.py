"""Tra cứu kết quả nạp trong lĩnh vực người dùng được cấp quyền."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ingestion.contracts.jobs import (
    IngestionAttemptRead,
    IngestionJobRead,
    IngestionJobStatus,
)
from app.modules.staff.services.access import ingestion_read_domains
from app.repositories.ingestion_repo import IngestionRepository


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
