"""Các API đọc lịch sử làm sạch và báo cáo của từng lượt."""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from minio.error import MinioException
from pydantic import AwareDatetime, BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool
from urllib3.exceptions import HTTPError

from app.core.config import settings
from app.core.deps import require_role
from app.core.exceptions import AppException
from app.core.roles import RoleCode
from app.dependencies import get_db
from app.modules.pipeline.models import CLEANING_RUNS
from app.modules.pipeline.repositories.silver_storage import MinioSilverStore, ObjectRef
from app.providers import silver_writer_client

router = APIRouter(
    prefix="/cleaning-runs",
    tags=["cleaning-runs"],
    dependencies=[Depends(require_role(RoleCode.ADMIN, RoleCode.DATA_ADMIN))],
)
DB = Annotated[AsyncSession, Depends(get_db)]
CleaningStatus = Literal["RUNNING", "COMPLETED", "FAILED"]
PublicationStatus = Literal[
    "NOT_STARTED",
    "RUNNING",
    "PUBLISHED",
    "PUBLISHED_WITH_QUARANTINE",
    "BLOCKED",
    "FAILED",
    "SKIPPED",
]


class CleaningRunRead(BaseModel):
    run_id: UUID
    ingestion_job_id: int
    dataset_id: int
    domain: str
    entity: str
    requested_by: str
    session: str
    bronze_version: str
    input_version: str | None
    cleaning_status: CleaningStatus
    publication_status: PublicationStatus
    records_read: int | None
    records_passed: int | None
    records_quarantined: int | None
    records_collapsed: int | None
    records_published: int
    silver_snapshot_id: str | None
    report_available: bool
    profile_evidence: dict[str, Any] | None = None
    error_code: str | None
    started_at: AwareDatetime
    finished_at: AwareDatetime | None


class CleaningRunFilters(BaseModel):
    dataset_id: int | None = Field(default=None, gt=0)
    ingestion_job_id: int | None = Field(default=None, gt=0)
    cleaning_status: CleaningStatus | None = None
    publication_status: PublicationStatus | None = None
    started_from: AwareDatetime | None = None
    started_to: AwareDatetime | None = None
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


def _metadata_unavailable() -> AppException:
    return AppException(
        "Không thể đọc lịch sử làm sạch", code="PIPELINE_METADATA_UNAVAILABLE", status_code=503
    )


async def _get_run(run_id: UUID, db: DB) -> dict[str, Any]:
    try:
        row = (
            (await db.execute(select(CLEANING_RUNS).where(CLEANING_RUNS.c.run_id == str(run_id))))
            .mappings()
            .one_or_none()
        )
    except SQLAlchemyError:
        raise _metadata_unavailable() from None
    if row is None:
        raise AppException(
            "Không tìm thấy lượt làm sạch", code="CLEANING_RUN_NOT_FOUND", status_code=404
        )
    return dict(row)


def _read_model(row: dict[str, Any]) -> CleaningRunRead:
    # Danh sách và chi tiết chỉ đọc PostgreSQL. Không trả đường dẫn nội bộ của MinIO.
    return CleaningRunRead.model_validate(
        {**row, "report_available": row["report_ref"] is not None}
    )


@router.get("", response_model=list[CleaningRunRead])
async def list_cleaning_runs(
    db: DB, filters: Annotated[CleaningRunFilters, Query()]
) -> list[CleaningRunRead]:
    if (
        filters.started_from is not None
        and filters.started_to is not None
        and filters.started_from >= filters.started_to
    ):
        raise AppException(
            "Khoảng thời gian không hợp lệ", code="INVALID_CLEANING_RUN_RANGE", status_code=422
        )
    columns = CLEANING_RUNS.c
    query = select(CLEANING_RUNS)
    for name in ("dataset_id", "ingestion_job_id", "cleaning_status", "publication_status"):
        value = getattr(filters, name)
        if value is not None:
            query = query.where(columns[name] == value)
    if filters.started_from is not None:
        query = query.where(columns.started_at >= filters.started_from)
    if filters.started_to is not None:
        query = query.where(columns.started_at < filters.started_to)
    query = query.order_by(columns.started_at.desc(), columns.run_id.desc())
    try:
        rows = (await db.execute(query.limit(filters.limit).offset(filters.offset))).mappings()
        return [_read_model(dict(row)) for row in rows]
    except SQLAlchemyError:
        raise _metadata_unavailable() from None


@router.get("/{run_id}", response_model=CleaningRunRead)
async def get_cleaning_run(run: Annotated[dict[str, Any], Depends(_get_run)]) -> CleaningRunRead:
    return _read_model(run)


def _read_report(reference: dict[str, Any]) -> dict[str, Any]:
    storage = MinioSilverStore(silver_writer_client(), settings.minio_bucket_silver)
    # Đọc đúng object và xác minh checksum theo tham chiếu đã lưu của lượt chạy.
    payload = json.loads(storage.get(ObjectRef(**reference)))
    if not isinstance(payload, dict):
        raise ValueError("INVALID_CLEANING_REPORT")
    return dict(payload)


@router.get("/{run_id}/report")
async def get_cleaning_report(run: Annotated[dict[str, Any], Depends(_get_run)]) -> dict[str, Any]:
    if run["report_ref"] is None:
        raise AppException(
            "Lượt làm sạch chưa có báo cáo", code="CLEANING_REPORT_NOT_FOUND", status_code=404
        )
    try:
        return await run_in_threadpool(_read_report, run["report_ref"])
    except (MinioException, HTTPError, OSError, ValueError, TypeError):
        raise AppException(
            "Không thể đọc báo cáo làm sạch",
            code="CLEANING_REPORT_UNAVAILABLE",
            status_code=503,
        ) from None
