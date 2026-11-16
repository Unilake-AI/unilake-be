"""Nhận việc Silver từ PostgreSQL, gia hạn độc lập và thử lại lỗi hạ tầng có giới hạn."""

from __future__ import annotations

import logging
from pathlib import Path
from threading import Event, Thread
from typing import TYPE_CHECKING

from minio.error import MinioException, S3Error
from sqlalchemy.exc import SQLAlchemyError
from urllib3.exceptions import HTTPError

from app.core.config import settings
from app.modules.pipeline.repositories.auto_pipeline import AutoPipelineRepository
from app.modules.pipeline.services.source_cleaning import run_manual_cleaning

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)
HEALTH_FILE = Path("/tmp/unilake_silver_worker_heartbeat")


def classify_failure(exc: Exception) -> tuple[str, bool]:
    """Không chạy lại lỗi dữ liệu, quyền truy cập hoặc thiếu cấu hình; chỉ thử lỗi tạm thời."""
    if isinstance(exc, S3Error) and exc.code in {
        "AccessDenied",
        "NoSuchKey",
        "NoSuchVersion",
        "NoSuchBucket",
        "InvalidAccessKeyId",
        "SignatureDoesNotMatch",
    }:
        return "AUTO_PIPELINE_STORAGE_INPUT_OR_ACCESS_ERROR", False
    if isinstance(exc, SQLAlchemyError) and getattr(
        getattr(exc, "orig", None), "sqlstate", None
    ) in {"42P01", "42703", "42804", "42883"}:
        return "AUTO_PIPELINE_SCHEMA_NOT_READY", False
    if isinstance(exc, SQLAlchemyError):
        state = getattr(getattr(exc, "orig", None), "sqlstate", "") or ""
        if state.startswith(("22", "23", "28", "42")):
            # Sai dữ liệu, ràng buộc hoặc quyền không thể tự sửa bằng cách đợi rồi chạy lại.
            return "AUTO_PIPELINE_DATABASE_CONFIGURATION_INVALID", False
    if isinstance(exc, (SQLAlchemyError, MinioException, HTTPError, ConnectionError, TimeoutError)):
        return "AUTO_PIPELINE_INFRASTRUCTURE_TRANSIENT", True
    if "ACTIVE_SNAPSHOT_CHANGED" in str(exc):
        return "AUTO_PIPELINE_SILVER_HEAD_CHANGED", True
    if isinstance(exc, ValueError):
        return "AUTO_PIPELINE_DATA_OR_CONFIGURATION_INVALID", False
    return "AUTO_PIPELINE_PROCESSING_FAILED", False


def run_once(engine: Engine, worker_id: str) -> bool:
    repository = AutoPipelineRepository(engine)
    job = repository.claim(worker_id, settings.silver_lease_seconds)
    repository.heartbeat(worker_id, job, settings.silver_lease_seconds)
    HEALTH_FILE.touch()
    if job is None:
        return False
    stop = Event()

    def pulse() -> None:
        while not stop.wait(settings.silver_heartbeat_seconds):
            try:
                if not repository.heartbeat(worker_id, job, settings.silver_lease_seconds):
                    return
                HEALTH_FILE.touch()
            except Exception as exc:
                logger.warning("Không gia hạn được lượt Silver (%s)", type(exc).__name__)

    thread = Thread(target=pulse, daemon=True)
    thread.start()
    try:
        result = run_manual_cleaning(
            job["ingestion_job_id"],
            requested_by=job["policy"]["requested_by"],
            profile_id=job["policy"]["profile_id"],
            allow_partial_publication=job["policy"]["allow_partial_publication"],
            base_snapshot="active",
            auto_context=job,
            engine_override=engine,
        )
        repository.finish(job, result)
    except Exception as exc:
        code, retryable = classify_failure(exc)
        try:
            repository.fail(job, code, retryable)
        except ValueError:
            # Quyền đã hết hoặc kết quả đã công bố; không được ghi đè trạng thái của lượt khác.
            logger.warning("Lượt Silver không còn quyền cập nhật: %s", job["job_id"])
    finally:
        stop.set()
        thread.join(timeout=5)
        repository.heartbeat(worker_id, None, settings.silver_lease_seconds)
    return True


def main(engine: Engine, worker_id: str) -> None:
    stop = Event()
    while not stop.is_set():
        try:
            worked = run_once(engine, worker_id)
        except Exception as exc:
            logger.error("Không xử lý được hàng đợi Silver (%s)", type(exc).__name__)
            worked = False
        if not worked:
            stop.wait(settings.silver_poll_seconds)
