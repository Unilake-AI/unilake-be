"""Cung cấp chức năng chạy làm sạch và lưu dữ liệu vào Silver."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query
from minio.error import S3Error
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.core.deps import require_role
from app.core.exceptions import AppException
from app.core.roles import RoleCode
from app.modules.pipeline.management_db import management_engine
from app.modules.pipeline.source_cleaning import preview_cleaning, run_manual_cleaning

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


class ManualCleaningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str = Field(min_length=1, max_length=100)
    base_snapshot: str | None = Field(default="active", min_length=1, max_length=36)


class ManualCleaningResponse(BaseModel):
    run_id: str
    status: str
    ingestion_job_id: int
    requested_by: str
    domain: str
    entity: str
    input_version: str
    records_read: int
    records_passed: int
    records_quarantined: int
    records_collapsed: int
    issues: list[dict[str, Any]]
    publication: dict[str, Any] | None
    quarantine_report: dict[str, Any] | None = None


Admin = Annotated[dict[str, Any], Depends(require_role(RoleCode.ADMIN, RoleCode.DATA_ADMIN))]


class CleaningPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: str = Field(min_length=1, max_length=100)


@router.post(
    "/cleaning/ingestion-jobs/{ingestion_job_id}/preview",
    tags=["silver-preview"],
    summary="Xem trước làm sạch và chuẩn hóa từ Bronze",
    description="Kiểm tra toàn bộ file và trả mẫu tối đa 100 dòng. Không lưu bản ghi lỗi, không ghi Silver, không thay đổi phiên bản hiện hành.",
)
def preview_cleaning_for_job(
    ingestion_job_id: Annotated[int, Path(gt=0)],
    request: CleaningPreviewRequest,
    _admin: Admin,
    db: Annotated[Engine, Depends(management_engine)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    try:
        return preview_cleaning(db, ingestion_job_id, request.profile_id, limit)
    except LookupError:
        raise AppException(
            "Không tìm thấy ingestion job", code="INGESTION_JOB_NOT_FOUND", status_code=404
        ) from None


@router.post(
    "/cleaning/ingestion-jobs/{ingestion_job_id}",
    response_model=ManualCleaningResponse,
    summary="Làm sạch và lưu Silver từ dữ liệu Bronze đã thu thập thành công",
    description="Áp dụng cấu hình nguồn được khai báo trên máy chủ; kiểm tra dữ liệu trước khi công bố Silver.",
)
def run_cleaning_for_ingestion_job(
    ingestion_job_id: Annotated[int, Path(gt=0)],
    request: ManualCleaningRequest,
    _admin: Admin,
) -> dict[str, Any]:
    try:
        return run_manual_cleaning(
            ingestion_job_id,
            requested_by=str(_admin.get("sub", "")),
            profile_id=request.profile_id,
            base_snapshot=request.base_snapshot,
        )
    except LookupError as exc:
        raise AppException("Không tìm thấy ingestion job", code=str(exc), status_code=404) from None
    except SQLAlchemyError:
        raise AppException(
            "Không thể truy cập metadata pipeline",
            code="PIPELINE_METADATA_UNAVAILABLE",
            status_code=503,
        ) from None
    except S3Error:
        raise AppException(
            "Không thể đọc Bronze hoặc ghi Silver",
            code="PIPELINE_OBJECT_STORAGE_UNAVAILABLE",
            status_code=503,
        ) from None
    except ValueError as exc:
        error_code = str(exc).split(":", maxsplit=1)[0]
        status_code = 409 if error_code == "SUCCESSFUL_BRONZE_JOB_REQUIRED" else 422
        raise AppException(
            "Không thể chạy Cleaning Pipeline",
            code=error_code,
            status_code=status_code,
        ) from None


__all__ = ["router"]
