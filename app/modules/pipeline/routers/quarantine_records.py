"""Các API tra hồ sơ cách ly, thống kê và xem dòng Bronze."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID
from zipfile import BadZipFile

from fastapi import APIRouter, Depends, Query, Response
from minio.error import MinioException
from pydantic import BaseModel, Field
from sqlalchemy import String, cast, func, literal, select, true, union_all
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool
from urllib3.exceptions import HTTPError

from app.core.deps import require_role
from app.core.exceptions import AppException
from app.core.roles import RoleCode
from app.dependencies import get_db
from app.modules.pipeline.cleaning.quarantine import SourceReadLimitError, read_quarantine_source
from app.modules.pipeline.models import CLEANING_RUNS, DATASET_CATALOG, QUARANTINE_RECORDS

router = APIRouter(
    prefix="/quarantine-records",
    tags=["quarantine-records"],
    dependencies=[Depends(require_role(RoleCode.ADMIN, RoleCode.DATA_ADMIN))],
)
DB = Annotated[AsyncSession, Depends(get_db)]
Q = QUARANTINE_RECORDS.c
R = CLEANING_RUNS.c
D = DATASET_CATALOG.c


class QuarantineFilters(BaseModel):
    cleaning_run_id: UUID | None = None
    ingestion_job_id: int | None = Field(default=None, gt=0)
    dataset_id: int | None = Field(default=None, gt=0)
    owner_department_id: int | None = Field(default=None, gt=0)
    error_code: str | None = Field(default=None, min_length=1, max_length=100)


class QuarantineListFilters(QuarantineFilters):
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class QuarantineRecordRead(BaseModel):
    record_id: UUID
    cleaning_run_id: UUID
    ingestion_job_id: int
    dataset_id: int
    domain: str
    entity: str
    owner_department_id: int | None
    locator: str
    record_position: int
    bronze_version: str
    error_codes: list[str]


class QuarantineRecordDetail(QuarantineRecordRead):
    issues: list[dict[str, Any]]
    source: dict[str, Any]


class QuarantineSummary(BaseModel):
    total_records: int
    by_dataset: list[dict[str, Any]]
    by_owner_department: list[dict[str, Any]]
    by_error_code: list[dict[str, Any]]


# Hàm query lấy danh sách hồ sơ cách ly với các bộ lọc
def _record_query(filters: QuarantineFilters) -> Any:
    query = (
        select(
            Q.record_id,
            Q.cleaning_run_id,
            Q.locator,
            Q.record_position,
            Q.error_codes,
            R.ingestion_job_id,
            R.dataset_id,
            R.domain,
            R.entity,
            R.bronze_version,
            R.started_at,
            D.owner_department_id,
        )
        .select_from(QUARANTINE_RECORDS)
        .join(CLEANING_RUNS, R.run_id == Q.cleaning_run_id)
        .join(DATASET_CATALOG, D.dataset_id == R.dataset_id)
    )
    for value, column in (
        (str(filters.cleaning_run_id) if filters.cleaning_run_id else None, Q.cleaning_run_id),
        (filters.ingestion_job_id, R.ingestion_job_id),
        (filters.dataset_id, R.dataset_id),
        (filters.owner_department_id, D.owner_department_id),
    ):
        if value is not None:
            query = query.where(column == value)
    if filters.error_code is not None:
        query = query.where(Q.error_codes.contains([filters.error_code]))
    return query


async def _execute(db: AsyncSession, query: Any) -> list[dict[str, Any]]:
    try:
        return [dict(row) for row in (await db.execute(query)).mappings()]
    except SQLAlchemyError:
        raise AppException(
            "Không thể truy cập hồ sơ cách ly",
            code="QUARANTINE_METADATA_UNAVAILABLE",
            status_code=503,
        ) from None


# Hàm query lấy chi tiết hồ sơ cách ly
async def _get_record(record_id: UUID, db: DB) -> dict[str, Any]:
    query = (
        _record_query(QuarantineFilters())
        .add_columns(
            Q.issues,
            R.quarantine_source,
            D.dataset_code,
            D.status.label("dataset_status"),
        )
        .where(Q.record_id == str(record_id))
    )
    rows = await _execute(db, query)
    if not rows:
        raise AppException(
            "Không tìm thấy hồ sơ cách ly", code="QUARANTINE_RECORD_NOT_FOUND", status_code=404
        )
    return rows[0]


# Hàm lấy metadata của hồ sơ cách ly
def _source_metadata(record: dict[str, Any]) -> dict[str, Any]:
    evidence = record["quarantine_source"]
    metadata = {
        "file_name": evidence["file_name"],
        "source_type": evidence["source_type"],
        "bronze_version": evidence["version_id"],
        "sha256": evidence["sha256"],
        "record_position": record["record_position"],
        "locator": record["locator"],
    }
    if evidence["source_type"] == "EXCEL":
        # Vị trí vật lý lấy từ locator của parser, không suy ra từ số thứ tự bản ghi.
        metadata["sheet_name"] = evidence["parser_options"]["sheet_name"]
        metadata["excel_row_number"] = int(record["locator"].rsplit(":row:", 1)[1])
    return metadata


@router.get("", response_model=list[QuarantineRecordRead])
async def list_quarantine_records(
    db: DB, filters: Annotated[QuarantineListFilters, Query()]
) -> list[QuarantineRecordRead]:
    query = _record_query(filters).order_by(
        R.started_at.desc(), Q.cleaning_run_id.desc(), Q.record_position, Q.record_id
    )
    rows = await _execute(db, query.limit(filters.limit).offset(filters.offset))
    return [QuarantineRecordRead.model_validate(row) for row in rows]


@router.get("/summary", response_model=QuarantineSummary)
async def summarize_quarantine_records(
    db: DB, filters: Annotated[QuarantineFilters, Query()]
) -> QuarantineSummary:
    # Dùng cùng bộ lọc với danh sách và tính tất cả nhóm trong một truy vấn.
    records = _record_query(filters).cte("filtered_quarantine")
    codes = (
        func.jsonb_array_elements_text(records.c.error_codes).table_valued("code").render_derived()
    )
    query = union_all(
        select(
            literal("total").label("dimension"),
            cast(None, String).label("key"),
            func.count().label("count"),
        ).select_from(records),
        select(literal("dataset"), cast(records.c.dataset_id, String), func.count()).group_by(
            records.c.dataset_id
        ),
        select(
            literal("owner"), cast(records.c.owner_department_id, String), func.count()
        ).group_by(records.c.owner_department_id),
        select(literal("code"), codes.c.code, func.count())
        .select_from(records.join(codes, true()))
        .group_by(codes.c.code),
    ).order_by("dimension", "key")
    summary = QuarantineSummary(
        total_records=0, by_dataset=[], by_owner_department=[], by_error_code=[]
    )
    for row in await _execute(db, query):
        dimension, key, count = row["dimension"], row["key"], row["count"]
        if dimension == "total":
            summary.total_records = count
        elif dimension == "dataset":
            summary.by_dataset.append({"dataset_id": int(key), "records": count})
        elif dimension == "owner":
            summary.by_owner_department.append(
                {"owner_department_id": int(key) if key is not None else None, "records": count}
            )
        else:
            summary.by_error_code.append({"error_code": key, "records": count})
    return summary


@router.get("/{record_id}", response_model=QuarantineRecordDetail)
async def get_quarantine_record(
    record: Annotated[dict[str, Any], Depends(_get_record)],
) -> QuarantineRecordDetail:
    return QuarantineRecordDetail.model_validate({**record, "source": _source_metadata(record)})


@router.get("/{record_id}/source")
async def get_quarantine_source(
    record: Annotated[dict[str, Any], Depends(_get_record)], response: Response
) -> dict[str, Any]:
    # Kiểm tra phạm vi dataset trước khi gọi MinIO, kể cả với tài khoản quản trị.
    allowed = set(record["quarantine_source"].get("allowed_columns", []))
    if not allowed or record["dataset_status"] != "ACTIVE":
        raise AppException(
            "Dataset không được phép xem dòng nguồn",
            code="QUARANTINE_SOURCE_FORBIDDEN",
            status_code=403,
        )
    try:
        data = await run_in_threadpool(read_quarantine_source, record)
    except SourceReadLimitError as exc:
        raise AppException("File nguồn vượt giới hạn xem", code=str(exc), status_code=413) from None
    except (MinioException, HTTPError, OSError, ValueError, BadZipFile):
        raise AppException(
            "Không thể xác minh hoặc đọc dòng Bronze",
            code="QUARANTINE_SOURCE_UNAVAILABLE",
            status_code=503,
        ) from None
    response.headers["Cache-Control"] = "no-store"
    invalid_fields = {
        field
        for issue in record["issues"]
        for field in (issue.get("key_fields") or [issue.get("field")])
        if field in allowed
    }
    return {
        "record_id": record["record_id"],
        "source": _source_metadata(record),
        "data": {key: value for key, value in data.items() if key in allowed},
        "invalid_fields": sorted(invalid_fields),
    }
