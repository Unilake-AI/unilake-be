"""Các tuyến API ingestion."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, require_role
from app.core.roles import RoleCode
from app.dependencies import get_db
from app.modules.ingestion.schemas import (
    IngestionAttemptRead,
    IngestionJobMetrics,
    IngestionJobRead,
    IngestionJobStats,
    IngestionJobStatus,
    IngestionStart,
    IngestionWorkerRead,
)
from app.modules.ingestion.services import jobs as ingestion_service
from app.modules.ingestion.services import uploads as uploads_service

if TYPE_CHECKING:
    from app.modules.ingestion.models import IngestionJob

UploadDomain = uploads_service.UploadDomain

router = APIRouter(prefix="/ingestion", tags=["ingestion"])

DB = Annotated[AsyncSession, Depends(get_db)]
User = Annotated[dict[str, Any], Depends(get_current_user)]
Admin = Annotated[dict[str, Any], Depends(require_role(RoleCode.ADMIN, RoleCode.DATA_ADMIN))]


@router.post("/jobs", response_model=IngestionJobRead, status_code=202)
async def start_ingestion(request: IngestionStart, db: DB, user: User) -> IngestionJob:
    return await ingestion_service.start_ingestion(request, db, user)


@router.get("/jobs", response_model=list[IngestionJobRead])
async def list_jobs(
    db: DB,
    user: User,
    data_source_id: Annotated[int | None, Query(gt=0)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    dataset_id: Annotated[int | None, Query(gt=0)] = None,
    status: IngestionJobStatus | None = None,
    mine_only: bool = False,
) -> list[IngestionJobRead]:
    return await ingestion_service.list_jobs(
        db, user, data_source_id, limit, offset, dataset_id, status, mine_only
    )


@router.get("/jobs/stats", response_model=IngestionJobStats)
async def job_stats(db: DB, user: User) -> dict[str, int]:
    return await ingestion_service.job_stats(db, user)


@router.get("/jobs/metrics", response_model=IngestionJobMetrics)
async def job_metrics(db: DB, user: User) -> dict[str, float | None]:
    return await ingestion_service.job_metrics(db, user)


@router.get("/workers", response_model=list[IngestionWorkerRead])
async def list_workers(db: DB, _user: Admin) -> list[dict[str, object]]:
    return await ingestion_service.list_workers(db)


@router.post("/jobs/{job_id}/retry", response_model=IngestionJobRead, status_code=202)
async def retry_job(job_id: int, db: DB, user: Admin) -> IngestionJob:
    return await ingestion_service.retry_job(job_id, db, user)


@router.get("/jobs/{job_id}/attempts", response_model=list[IngestionAttemptRead])
async def job_attempts(job_id: int, db: DB, user: User) -> list[IngestionAttemptRead]:
    return await ingestion_service.job_attempts(job_id, db, user)


@router.get("/jobs/{job_id}", response_model=IngestionJobRead)
async def get_job(job_id: int, db: DB, user: User) -> IngestionJobRead:
    return await ingestion_service.get_job(job_id, db, user)


@router.post(
    "/uploads",
    response_model=IngestionJobRead,
    status_code=202,
    summary="Tải file lên để kiểm tra và lưu vào Bronze",
    description=(
        "Chọn bộ dữ liệu đã có bằng dataset_id, hoặc tạo bộ mới bằng dataset_name. "
        "Với bộ mới, nhân viên có một lĩnh vực không cần nhập domain; hệ thống lấy từ quyền. "
        "Quản trị hoặc tài khoản nhiều lĩnh vực cần chọn domain hợp lệ. "
        "Không gửi thông tin tạo bộ mới khi đã chọn dataset_id."
    ),
)
async def upload_ingestion(
    db: DB,
    user: User,
    file: Annotated[UploadFile, File(description="File CSV, XLSX hoặc XLSM")],
    dataset_id: Annotated[
        int | None,
        Form(gt=0, description="Bộ dữ liệu đã chọn trên giao diện; bỏ trống khi tạo mới"),
    ] = None,
    parser_options: Annotated[str, Form(description="Tùy chọn đọc file dưới dạng JSON")] = "{}",
    dataset_name: Annotated[
        str | None, Form(min_length=1, max_length=200, description="Tên bộ dữ liệu mới")
    ] = None,
    description: Annotated[
        str | None, Form(max_length=4000, description="Mô tả bộ dữ liệu mới")
    ] = None,
    domain: Annotated[
        UploadDomain | None,
        Form(
            description="Chỉ cần chọn khi tạo mới và tài khoản có nhiều lĩnh vực hoặc là quản trị"
        ),
    ] = None,
) -> IngestionJobRead:
    return await uploads_service.upload_file(
        db, user, file, dataset_id, parser_options, dataset_name, description, domain
    )
