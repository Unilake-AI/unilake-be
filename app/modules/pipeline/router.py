"""Các tuyến API pipeline."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path
from minio.error import S3Error
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from app.core.deps import require_role
from app.core.exceptions import AppException
from app.modules.pipeline.manual_cleaning import run_manual_cleaning

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


class ManualCleaningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session: str = Field(default="default", pattern=r"^[A-Za-z0-9-]{1,40}$")
    base_snapshot: str | None = Field(default="active", min_length=1, max_length=36)


class ManualCleaningResponse(BaseModel):
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


Admin = Annotated[dict[str, Any], Depends(require_role("ADMIN", "DATA_ADMIN"))]


@router.post(
    "/cleaning/ingestion-jobs/{ingestion_job_id}",
    response_model=ManualCleaningResponse,
    summary="Run manual cleaning for a completed demo Bronze job",
    description=(
        "Runs the pinned Bronze object through cleaning and Silver rehearsal. "
        "Only the dedicated synthetic DEMO_FLOW catalog profiles are currently supported. "
        "Any quarantined row prevents Silver publication."
    ),
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
            session=request.session,
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
