"""Đọc bằng chứng nạp dữ liệu cho yêu cầu sửa, không đổi trạng thái yêu cầu."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from app.modules.datasources.models import DataLayer
from app.modules.ingestion.models import IngestionJob
from app.modules.pipeline.models import (
    CLEANING_RUNS,
    DATA_QUALITY_ISSUE_RECORDS,
    DATASET_CATALOG,
    QUARANTINE_RECORDS,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

R, Q, L, D = CLEANING_RUNS.c, QUARANTINE_RECORDS.c, DATA_QUALITY_ISSUE_RECORDS.c, DATASET_CATALOG.c


def _linked_jobs(issue_id: str) -> Any:
    return (
        select(IngestionJob)
        .select_from(DATA_QUALITY_ISSUE_RECORDS)
        .join(QUARANTINE_RECORDS, Q.record_id == L.record_id)
        .join(CLEANING_RUNS, R.run_id == Q.cleaning_run_id)
        .join(IngestionJob, IngestionJob.ingestion_job_id == R.ingestion_job_id)
        .where(L.issue_id == issue_id)
    )


async def issue_records(db: AsyncSession, issue_id: str) -> list[dict[str, Any]]:
    query = (
        _linked_jobs(issue_id)
        .with_only_columns(
            Q.record_id,
            Q.cleaning_run_id,
            Q.locator,
            Q.record_position,
            Q.error_codes,
            Q.issues,
            R.ingestion_job_id,
            R.bronze_version,
            R.entity,
            IngestionJob.source_table,
        )
        .order_by(R.started_at, Q.record_position, Q.record_id)
    )
    return [dict(row) for row in (await db.execute(query)).mappings()]


async def issue_candidates(db: AsyncSession, record_ids: set[str]) -> list[dict[str, Any]]:
    query = (
        select(
            Q.record_id,
            R.dataset_id,
            R.domain,
            D.owner_department_id,
            R.entity,
            IngestionJob.data_source_id,
            IngestionJob.source_table,
        )
        .select_from(QUARANTINE_RECORDS)
        .join(CLEANING_RUNS, Q.cleaning_run_id == R.run_id)
        .join(DATASET_CATALOG, D.dataset_id == R.dataset_id)
        .join(IngestionJob, IngestionJob.ingestion_job_id == R.ingestion_job_id)
        .where(Q.record_id.in_(record_ids))
    )
    return [dict(row) for row in (await db.execute(query)).mappings()]


async def issue_targets(db: AsyncSession, issue_id: str) -> list[dict[str, Any]]:
    query = (
        _linked_jobs(issue_id)
        .with_only_columns(IngestionJob.data_source_id, IngestionJob.source_table, R.entity)
        .distinct()
    )
    return [dict(row) for row in (await db.execute(query)).mappings()]


async def latest_issue_output(db: AsyncSession, issue_id: str) -> dict[str, Any] | None:
    query = (
        _linked_jobs(issue_id)
        .with_only_columns(IngestionJob.output)
        .order_by(IngestionJob.created_at.desc())
        .limit(1)
    )
    return (await db.execute(query)).scalar_one_or_none()


async def latest_invalid_created_at(db: AsyncSession, issue_id: str) -> datetime | None:
    return (
        await db.execute(
            _linked_jobs(issue_id).with_only_columns(func.max(IngestionJob.created_at))
        )
    ).scalar_one_or_none()


@dataclass(frozen=True)
class ResolutionJob:
    ingestion_job_id: int
    root_job_id: int | None
    job_type: str | None
    requested_by: str | None
    created_at: datetime | None
    upload_input: dict[str, Any] | None


async def resolution_job(
    db: AsyncSession,
    job_id: int,
    *,
    dataset_id: int | None = None,
    source_id: int | None = None,
    filename: str | None = None,
) -> ResolutionJob | None:
    query = select(IngestionJob).where(IngestionJob.ingestion_job_id == job_id)
    if dataset_id is not None:
        query = query.join(
            DataLayer, DataLayer.data_layer_id == IngestionJob.target_layer_id
        ).where(
            IngestionJob.dataset_id == dataset_id,
            IngestionJob.data_source_id == source_id,
            IngestionJob.source_table == filename,
            IngestionJob.status == "SUCCESS",
            IngestionJob.job_type.in_(["UPLOAD_V1", "EXECUTION_V1"]),
            DataLayer.layer_code == "BRONZE",
        )
    job = (await db.execute(query)).scalars().one_or_none()
    if job is None:
        return None
    return ResolutionJob(
        job.ingestion_job_id,
        job.root_job_id,
        job.job_type,
        job.requested_by,
        job.created_at,
        job.upload_input,
    )
