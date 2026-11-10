"""Các tuyến API ingestion."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from minio.error import MinioException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from urllib3.exceptions import HTTPError

from app.core.config import settings
from app.core.deps import get_current_user, require_role
from app.core.exceptions import AppException
from app.core.roles import RoleCode
from app.dependencies import get_db
from app.modules.ingestion.execution import IngestionExecutionService, ingestion_read_domains
from app.modules.ingestion.schemas import (
    IngestionAttemptRead,
    IngestionJobMetrics,
    IngestionJobRead,
    IngestionJobStats,
    IngestionStart,
    IngestionWorkerRead,
)
from app.modules.ingestion.uploads import receive_upload
from app.repositories.ingestion_repo import IngestionRepository

if TYPE_CHECKING:
    from app.modules.ingestion.models import IngestionJob

router = APIRouter(prefix="/ingestion", tags=["ingestion"])

DB = Annotated[AsyncSession, Depends(get_db)]
User = Annotated[dict[str, Any], Depends(get_current_user)]
Admin = Annotated[dict[str, Any], Depends(require_role(RoleCode.ADMIN, RoleCode.DATA_ADMIN))]


@router.post("/jobs", response_model=IngestionJobRead, status_code=202)
async def start_ingestion(request: IngestionStart, db: DB, user: User) -> IngestionJob:
    try:
        return await IngestionExecutionService(IngestionRepository(db)).start(request, user)
    except SQLAlchemyError:
        await db.rollback()
        raise AppException(
            "Không thể lưu job ingestion", code="INGESTION_QUEUE_UNAVAILABLE", status_code=503
        ) from None


@router.get("/jobs", response_model=list[IngestionJobRead])
async def list_jobs(
    db: DB,
    user: User,
    data_source_id: Annotated[int | None, Query(gt=0)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[IngestionJob]:
    return await IngestionRepository(db).list(
        data_source_id, limit, offset, ingestion_read_domains(user)
    )


@router.get("/jobs/stats", response_model=IngestionJobStats)
async def job_stats(db: DB, user: User) -> dict[str, int]:
    return await IngestionRepository(db).stats(ingestion_read_domains(user))


@router.get("/jobs/metrics", response_model=IngestionJobMetrics)
async def job_metrics(db: DB, user: User) -> dict[str, float | None]:
    return await IngestionRepository(db).metrics(ingestion_read_domains(user))


@router.get("/workers", response_model=list[IngestionWorkerRead])
async def list_workers(db: DB, _user: Admin) -> list[dict[str, object]]:
    now = datetime.now(UTC).replace(tzinfo=None)
    workers = await IngestionRepository(db).workers()
    return [
        {
            "worker_id": worker.worker_id,
            "status": "HEALTHY"
            if (now - worker.last_heartbeat_at).total_seconds()
            <= settings.worker_health_stale_seconds
            else "OFFLINE",
            "current_job_id": worker.current_job_id,
            "last_heartbeat_at": worker.last_heartbeat_at,
        }
        for worker in workers
    ]


@router.post("/jobs/{job_id}/retry", response_model=IngestionJobRead, status_code=202)
async def retry_job(job_id: int, db: DB, user: Admin) -> IngestionJob:
    try:
        return await IngestionRepository(db).retry(job_id, str(user.get("sub", "")))
    except SQLAlchemyError:
        await db.rollback()
        raise AppException(
            "Không thể tạo lần chạy lại", code="INGESTION_RETRY_UNAVAILABLE", status_code=503
        ) from None


@router.get("/jobs/{job_id}/attempts", response_model=list[IngestionAttemptRead])
async def job_attempts(job_id: int, db: DB, user: User) -> list[IngestionJob]:
    return await IngestionRepository(db).attempts(job_id, ingestion_read_domains(user))


@router.get("/jobs/{job_id}", response_model=IngestionJobRead)
async def get_job(job_id: int, db: DB, user: User) -> IngestionJob:
    return await IngestionRepository(db).get(job_id, ingestion_read_domains(user))


@router.post(
    "/uploads",
    response_model=IngestionJobRead,
    status_code=202,
    summary="Tải file lên để kiểm tra và lưu vào Bronze",
)
async def upload_ingestion(
    db: DB,
    user: User,
    file: Annotated[UploadFile, File(description="File CSV, XLSX hoặc XLSM")],
    dataset_id: Annotated[int, Form(gt=0)],
    parser_options: Annotated[str, Form(description="Tùy chọn đọc file dưới dạng JSON")] = "{}",
) -> IngestionJob:
    try:
        return await receive_upload(db, file, dataset_id, parser_options, user)
    except SQLAlchemyError:
        await db.rollback()
        raise AppException(
            "Không thể tạo job tải file", code="INGESTION_QUEUE_UNAVAILABLE", status_code=503
        ) from None
    except AppException:
        await db.rollback()
        raise
    except (MinioException, HTTPError, OSError, ValueError):
        await db.rollback()
        raise AppException(
            "Không thể lưu file tiếp nhận", code="UPLOAD_STORAGE_UNAVAILABLE", status_code=503
        ) from None
    finally:
        await file.close()
