"""Đọc và lưu yêu cầu sửa dữ liệu cùng bằng chứng nạp liên quan."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import false, func, insert, select, text, update

from app.modules.datasources.models import DataLayer, DataSource
from app.modules.ingestion.models import IngestionJob
from app.modules.pipeline.models import (
    CLEANING_RUNS,
    DATA_QUALITY_ISSUE_EVENTS,
    DATA_QUALITY_ISSUE_RECORDS,
    DATA_QUALITY_ISSUES,
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


def _issue_query(
    *,
    is_admin: bool,
    departments: frozenset[int],
    domains: frozenset[str],
    user_id: int,
    department_id: int | None = None,
    assignee_user_id: int | None = None,
    status: str | None = None,
    mine_only: bool = False,
) -> Any:
    columns = DATA_QUALITY_ISSUES.c
    query = select(DATA_QUALITY_ISSUES)
    if not is_admin:
        query = (
            query.where(columns.department_id.in_(departments), columns.domain.in_(domains))
            if departments
            else query.where(false())
        )
    if department_id is not None:
        query = query.where(columns.department_id == department_id)
    if assignee_user_id is not None:
        query = query.where(columns.assignee_user_id == assignee_user_id)
    if status is not None:
        query = query.where(columns.status == status)
    if mine_only:
        query = query.where(columns.assignee_user_id == user_id)
    return query


async def get_issue(
    db: AsyncSession,
    issue_id: str,
    *,
    is_admin: bool,
    departments: frozenset[int],
    domains: frozenset[str],
    user_id: int,
    lock: bool = False,
) -> dict[str, Any] | None:
    query = _issue_query(
        is_admin=is_admin, departments=departments, domains=domains, user_id=user_id
    ).where(DATA_QUALITY_ISSUES.c.issue_id == issue_id)
    # Chỉ một thao tác được đổi trạng thái yêu cầu tại một thời điểm.
    if lock:
        query = query.with_for_update()
    row = (await db.execute(query)).mappings().one_or_none()
    return dict(row) if row is not None else None


async def linked_record_ids(db: AsyncSession, issue_id: str) -> list[str]:
    return list(
        (
            await db.execute(
                select(L.record_id).where(L.issue_id == issue_id).order_by(L.record_id)
            )
        ).scalars()
    )


async def issue_history(db: AsyncSession, issue_id: str) -> list[dict[str, Any]]:
    columns = DATA_QUALITY_ISSUE_EVENTS.c
    rows = (
        await db.execute(
            select(DATA_QUALITY_ISSUE_EVENTS)
            .where(columns.issue_id == issue_id)
            .order_by(columns.event_id)
        )
    ).mappings()
    return [dict(row) for row in rows]


async def active_department_id(db: AsyncSession, department_id: int) -> int | None:
    return (
        await db.execute(
            text(
                "SELECT department_id FROM app.department WHERE department_id = :id AND status = 'ACTIVE'"
            ),
            {"id": department_id},
        )
    ).scalar_one_or_none()


async def insert_issue(db: AsyncSession, values: dict[str, Any]) -> None:
    await db.execute(insert(DATA_QUALITY_ISSUES).values(**values))


async def link_records(db: AsyncSession, issue_id: str, record_ids: set[str]) -> None:
    await db.execute(
        insert(DATA_QUALITY_ISSUE_RECORDS),
        [{"issue_id": issue_id, "record_id": record_id} for record_id in sorted(record_ids)],
    )


async def add_event(db: AsyncSession, values: dict[str, Any]) -> None:
    await db.execute(insert(DATA_QUALITY_ISSUE_EVENTS).values(**values))


async def update_issue(db: AsyncSession, issue_id: str, values: dict[str, Any]) -> None:
    await db.execute(
        update(DATA_QUALITY_ISSUES)
        .where(DATA_QUALITY_ISSUES.c.issue_id == issue_id)
        .values(**values)
    )


async def list_issues(
    db: AsyncSession, *, limit: int, offset: int, **scope: Any
) -> list[dict[str, Any]]:
    columns = DATA_QUALITY_ISSUES.c
    query = (
        _issue_query(**scope)
        .order_by(columns.updated_at.desc(), columns.issue_id.desc())
        .limit(limit)
        .offset(offset)
    )
    return [dict(row) for row in (await db.execute(query)).mappings()]


async def issue_stats(db: AsyncSession, **scope: Any) -> dict[str, int]:
    items = _issue_query(**scope).subquery()
    query = select(
        func.count().label("total"),
        func.count().filter(items.c.status == "ASSIGNED").label("assigned"),
        func.count().filter(items.c.status == "PENDING_VERIFICATION").label("pending_verification"),
        func.count().filter(items.c.status == "VERIFIED").label("verified"),
    ).select_from(items)
    return dict((await db.execute(query)).mappings().one())


async def verification_run(
    db: AsyncSession, run_id: str, resolution_job_id: int | None, dataset_id: int
) -> dict[str, Any] | None:
    query = select(R.cleaning_status, R.publication_status, R.finished_at, R.entity).where(
        R.run_id == run_id, R.ingestion_job_id == resolution_job_id, R.dataset_id == dataset_id
    )
    row = (await db.execute(query)).mappings().one_or_none()
    return dict(row) if row is not None else None


async def issue_records_with_sources(db: AsyncSession, issue_id: str) -> list[dict[str, Any]]:
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
            R.domain,
            R.dataset_id,
            R.quarantine_source,
            IngestionJob.data_source_id,
            IngestionJob.source_table,
            IngestionJob.output.label("job_output"),
            IngestionJob.upload_input.label("job_upload_input"),
        )
        .order_by(R.started_at, Q.record_position, Q.record_id)
    )
    return [dict(row) for row in (await db.execute(query)).mappings()]


async def comparison_cleaning_run(
    db: AsyncSession,
    *,
    run_id: str | None = None,
    resolution_job_id: int | None = None,
    dataset_id: int | None = None,
    entity: str | None = None,
) -> dict[str, Any] | None:
    query = select(
        R.run_id,
        R.ingestion_job_id,
        R.dataset_id,
        R.domain,
        R.entity,
        R.bronze_version,
        R.cleaning_status,
        R.publication_status,
        R.records_read,
        R.records_passed,
        R.records_quarantined,
        R.records_collapsed,
        R.records_published,
        R.silver_snapshot_id,
        R.quarantine_source,
        R.started_at,
        R.finished_at,
        IngestionJob.data_source_id,
        IngestionJob.source_table,
        IngestionJob.output.label("job_output"),
        IngestionJob.upload_input.label("job_upload_input"),
    ).select_from(
        CLEANING_RUNS.join(IngestionJob, IngestionJob.ingestion_job_id == R.ingestion_job_id)
    )
    if run_id is not None:
        query = query.where(R.run_id == run_id)
    else:
        query = query.where(R.ingestion_job_id == resolution_job_id)
        if dataset_id is not None:
            query = query.where(R.dataset_id == dataset_id)
        if entity is not None:
            query = query.where(R.entity == entity)
        query = query.order_by(R.started_at.desc(), R.run_id.desc()).limit(1)
    row = (await db.execute(query)).mappings().one_or_none()
    return dict(row) if row is not None else None


async def run_quarantine_records(db: AsyncSession, cleaning_run_id: str) -> list[dict[str, Any]]:
    query = (
        select(Q.record_id, Q.locator, Q.record_position, Q.error_codes, Q.issues)
        .where(Q.cleaning_run_id == cleaning_run_id)
        .order_by(Q.record_position, Q.record_id)
    )
    return [dict(row) for row in (await db.execute(query)).mappings()]


async def source_metadata_map(
    db: AsyncSession, data_source_ids: set[int]
) -> dict[int, dict[str, Any]]:
    if not data_source_ids:
        return {}
    rows = (
        await db.execute(
            select(
                DataSource.data_source_id,
                DataSource.source_system_id,
                DataSource.source_type,
                DataSource.configuration,
            ).where(DataSource.data_source_id.in_(data_source_ids))
        )
    ).mappings()
    return {int(row["data_source_id"]): dict(row) for row in rows}
