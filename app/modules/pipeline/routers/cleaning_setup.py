"""Giúp quản trị viên xem gợi ý và xác nhận cách xử lý mà không nhập cấu hình kỹ thuật."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query
from minio.error import S3Error
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.modules.pipeline.repositories.management_db import management_engine
from app.modules.pipeline.routers.pipeline import Admin, _to_cleaning_http_exception
from app.modules.pipeline.routers.profiles import ProfileRead
from app.modules.pipeline.services.cleaning_setup import (
    CleaningChoices,
    ConfirmCleaningChoices,
    analyze_setup,
    confirm_setup,
    preview_setup,
)

router = APIRouter(
    prefix="/pipeline/cleaning/ingestion-jobs/{ingestion_job_id}/setup",
    tags=["silver-setup"],
)
JobID = Annotated[int, Path(gt=0)]
DB = Annotated[Engine, Depends(management_engine)]


@router.get("", summary="Đọc file Bronze và gợi ý cách xử lý")
def get_setup(ingestion_job_id: JobID, db: DB, _admin: Admin) -> dict[str, Any]:
    try:
        return analyze_setup(db, ingestion_job_id)
    except (LookupError, SQLAlchemyError, S3Error, ValueError) as exc:
        raise _to_cleaning_http_exception(exc) from None


@router.post("/preview", summary="Xem trước cách xử lý được đề xuất, chưa lưu cấu hình")
def preview(
    ingestion_job_id: JobID,
    choices: CleaningChoices,
    db: DB,
    admin: Admin,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    try:
        return preview_setup(db, ingestion_job_id, choices, str(admin["sub"]), limit)
    except (LookupError, SQLAlchemyError, S3Error, ValueError) as exc:
        raise _to_cleaning_http_exception(exc) from None


@router.post(
    "/confirm", response_model=ProfileRead, summary="Xác nhận và lưu cách xử lý đã xem trước"
)
def confirm(
    ingestion_job_id: JobID, request: ConfirmCleaningChoices, db: DB, admin: Admin
) -> dict[str, Any]:
    try:
        return confirm_setup(db, ingestion_job_id, request, str(admin["sub"]))
    except (LookupError, SQLAlchemyError, S3Error, ValueError) as exc:
        raise _to_cleaning_http_exception(exc) from None
