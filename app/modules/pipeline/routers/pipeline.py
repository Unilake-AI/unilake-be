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
from app.modules.pipeline.repositories.management_db import management_engine
from app.modules.pipeline.services.source_cleaning import preview_cleaning, run_manual_cleaning

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


class ManualCleaningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str = Field(min_length=1, max_length=100)
    base_snapshot: str | None = Field(default="active", min_length=1, max_length=36)
    # [SỬA - Vấn đề 1] Cho phép công bố riêng phần dữ liệu sạch khi tệp vẫn còn một số dòng lỗi.
    allow_partial_publication: bool = True


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
    profile_id: str | None = None
    profile_fingerprint: str | None = None
    profile_approval: dict[str, Any] | None = None
    partial_key_check: dict[str, Any] | None = None


Admin = Annotated[dict[str, Any], Depends(require_role(RoleCode.ADMIN, RoleCode.DATA_ADMIN))]


class CleaningPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: str = Field(min_length=1, max_length=100)


# [SỬA - Vấn đề 1] Bảng thông báo lỗi tiếng Việt thân thiện cho người dùng cuối (non-tech),
# tách biệt với mã lỗi nội bộ để dễ hiển thị trên giao diện.
_CLEANING_ERROR_MESSAGES: dict[str, str] = {
    "INVALID_INGESTION_JOB_ID": "Mã lần tải dữ liệu không hợp lệ",
    "INVALID_MANUAL_CLEANING_OPTIONS": "Tùy chọn xử lý dữ liệu không hợp lệ",
    "SOURCE_OR_DATASET_INACTIVE": "Nguồn dữ liệu hoặc bộ dữ liệu này đang tạm ngưng hoạt động",
    "SUCCESSFUL_BRONZE_JOB_REQUIRED": "Chỉ có thể làm sạch từ lần tải dữ liệu đã hoàn tất thành công",
    "BRONZE_DATASET_SOURCE_MISMATCH": "Thông tin nguồn dữ liệu và bộ dữ liệu không khớp nhau",
    "UNSUPPORTED_BRONZE_FILE_TYPE": "Định dạng tệp dữ liệu này chưa được hỗ trợ",
    "BRONZE_SOURCE_FILE_TYPE_MISMATCH": "Tên tệp dữ liệu không khớp với định dạng đã khai báo",
    "BRONZE_JOB_OUTPUT_REQUIRED": "Không tìm thấy thông tin tệp dữ liệu đã tải lên",
    "BRONZE_JOB_OUTPUT_INVALID_OR_UNSUPPORTED": "Đường dẫn tệp dữ liệu đã tải lên không hợp lệ",
    "BRONZE_JOB_EVIDENCE_REQUIRED": "Thiếu thông tin đối chiếu của tệp dữ liệu đã tải lên",
    "BRONZE_JOB_OBJECT_EVIDENCE_MISMATCH": "Tệp dữ liệu đã tải lên không khớp với thông tin lưu trữ",
    "BRONZE_JOB_CAPTURE_MISMATCH": "Nội dung tệp dữ liệu không khớp với lần tải lên ban đầu",
    "BRONZE_REUSE_ORIGIN_REQUIRED": "Thiếu thông tin tệp dữ liệu gốc để dùng lại",
    "BRONZE_REUSE_ORIGIN_MISMATCH": "Thông tin tệp dữ liệu gốc dùng lại không khớp với lần tải trước đó",
    "SILVER_PROFILE_SOURCE_MISMATCH": "Cấu hình xử lý được chọn không áp dụng cho nguồn dữ liệu này",
    "BRONZE_PARSER_EVIDENCE_REQUIRED": "Thiếu thông tin cách đọc tệp dữ liệu đã tải lên",
    "SOURCE_LAYOUT_MISMATCH": "Các cột trong tệp dữ liệu không khớp với cấu hình đã khai báo",
    "SILVER_PROFILE_CONFIGURATION_UNAVAILABLE": "Không thể đọc tệp cấu hình xử lý dữ liệu",
    "SILVER_SOURCE_PROFILE_NOT_FOUND": "Không tìm thấy cấu hình xử lý dữ liệu được chọn",
    "SILVER_SOURCE_PROFILE_INVALID": "Cấu hình xử lý dữ liệu chưa hợp lệ",
}

_PUBLICATION_ERROR_MESSAGES: dict[str, str] = {
    "PARTIAL_PUBLICATION_NOT_ALLOWED": (
        "Không thể công bố một phần dữ liệu vì chưa có dòng sạch hợp lệ "
        "hoặc cấu hình lô dữ liệu không hỗ trợ"
    ),
    "ACTIVE_SNAPSHOT_CHANGED": (
        "Dữ liệu đang sử dụng vừa được người khác cập nhật, vui lòng tải lại và thực hiện lại"
    ),
    "SNAPSHOT_REJECTED": (
        "Các dòng dữ liệu sạch vẫn chưa đáp ứng đầy đủ quy tắc kiểm tra chung "
        "nên chưa thể đưa vào sử dụng"
    ),
    "BUSINESS_GATE_REJECTED": "Dữ liệu chưa đạt điều kiện kiểm tra nghiệp vụ để đưa vào sử dụng",
    "STORAGE_OR_METADATA_FAILURE": "Hệ thống lưu trữ đang gặp sự cố tạm thời, vui lòng thử lại sau",
}


def _to_cleaning_http_exception(exc: Exception) -> AppException:
    """Chuyển đổi lỗi xử lý nội bộ sang thông báo tiếng Việt dễ hiểu cho người dùng."""
    if isinstance(exc, LookupError):
        return AppException(
            "Không tìm thấy lần tải dữ liệu được chọn",
            code=str(exc),
            status_code=404,
        )
    if isinstance(exc, SQLAlchemyError):
        return AppException(
            "Hệ thống lưu trữ thông tin xử lý đang bận hoặc tạm thời gián đoạn, vui lòng thử lại sau",
            code="PIPELINE_METADATA_UNAVAILABLE",
            status_code=503,
        )
    if isinstance(exc, S3Error):
        return AppException(
            "Không thể đọc tệp dữ liệu nguồn hoặc lưu kết quả xử lý, vui lòng thử lại sau",
            code="PIPELINE_OBJECT_STORAGE_UNAVAILABLE",
            status_code=503,
        )
    error_code, _, detail = str(exc).partition(":")
    status_code = 409 if error_code == "SUCCESSFUL_BRONZE_JOB_REQUIRED" else 422
    if error_code == "SILVER_PUBLICATION_REJECTED":
        message = _PUBLICATION_ERROR_MESSAGES.get(
            detail,
            "Dữ liệu sau khi làm sạch chưa đạt yêu cầu kiểm tra nên chưa thể lưu lại",
        )
    else:
        message = _CLEANING_ERROR_MESSAGES.get(
            error_code,
            "Không thể xử lý dữ liệu theo yêu cầu, vui lòng kiểm tra lại cấu hình và tệp nguồn",
        )
    return AppException(message, code=error_code, status_code=status_code)


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
    except (LookupError, SQLAlchemyError, S3Error, ValueError) as exc:
        raise _to_cleaning_http_exception(exc) from None


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
            allow_partial_publication=request.allow_partial_publication,
        )
    except (LookupError, SQLAlchemyError, S3Error, ValueError) as exc:
        raise _to_cleaning_http_exception(exc) from None


__all__ = ["router"]
