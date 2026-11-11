"""Nhận việc trong hàng đợi, theo dõi quá trình chạy và dọn file tiếp nhận hết hạn."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy.ext.asyncio import AsyncSession

from app.bronze_capture import BronzeCapture, store_bronze
from app.core.config import settings
from app.modules.ingestion.services.execution import IngestionExecutionService
from app.modules.ingestion.services.upload_capture import UploadCapture
from app.providers import upload_cleanup_client
from app.repositories.ingestion_repo import IngestionRepository, utcnow

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

logger = logging.getLogger(__name__)
HEALTH_FILE = Path("/tmp/unilake_ingestion_worker_heartbeat")


@dataclass
class WorkerState:
    current_job_id: int | None = None


async def report_worker_heartbeat(state: WorkerState, session_factory: SessionFactory) -> None:
    """Lưu dấu hiệu worker còn dùng được hàng đợi, kể cả khi đang rảnh."""
    async with session_factory() as session:
        await IngestionRepository(session).heartbeat_worker(
            settings.ingestion_worker_id, state.current_job_id
        )
    # Docker chỉ cần biết worker còn liên lạc được PostgreSQL gần đây.
    HEALTH_FILE.touch()


async def worker_heartbeat(state: WorkerState, session_factory: SessionFactory) -> None:
    while True:
        try:
            await report_worker_heartbeat(state, session_factory)
        except Exception as exc:
            logger.warning("Worker heartbeat failed (%s)", type(exc).__name__)
        await asyncio.sleep(settings.worker_heartbeat_seconds)


async def cleanup_staging(session_factory: SessionFactory) -> None:
    """Xóa đúng phiên bản file tiếp nhận sau khi cả chuỗi retry đã kết thúc."""
    before = utcnow() - timedelta(hours=settings.upload_staging_retention_hours)
    async with session_factory() as session:
        inputs = await IngestionRepository(session).expired_staging_inputs(before)
    storage = upload_cleanup_client()
    for evidence in inputs:
        uri = urlsplit(str(evidence.get("uri", "")))
        version = evidence.get("version_id")
        if (
            uri.scheme == "s3"
            and uri.netloc == settings.minio_bucket_uploads
            and isinstance(version, str)
        ):
            storage.remove_object(
                settings.minio_bucket_uploads, uri.path.lstrip("/"), version_id=version
            )


async def job_heartbeat(job_id: int, session_factory: SessionFactory) -> None:
    while True:
        await asyncio.sleep(15)
        async with session_factory() as session:
            if not await IngestionRepository(session).heartbeat(job_id):
                raise RuntimeError("INGESTION_LEASE_LOST")


async def run_once(state: WorkerState, session_factory: SessionFactory) -> bool:
    async with session_factory() as session:
        repo = IngestionRepository(session)
        await repo.recover_expired()
        job = await repo.claim()
        if job is None:
            return False
        state.current_job_id = job.ingestion_job_id
        await report_worker_heartbeat(state, session_factory)
        pulse = asyncio.create_task(job_heartbeat(job.ingestion_job_id, session_factory))
        # Tải lên và thu thập nguồn dùng chung hàng đợi; chỉ khác cách lấy bản file đầu vào.
        execution = asyncio.create_task(
            IngestionExecutionService(repo).execute(
                job,
                (lambda: UploadCapture(job)) if job.job_type == "UPLOAD_V1" else BronzeCapture,
                store_bronze,
            )
        )
        try:
            done, _ = await asyncio.wait({pulse, execution}, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                await task
        finally:
            for task in (pulse, execution):
                task.cancel()
            await asyncio.gather(pulse, execution, return_exceptions=True)
            state.current_job_id = None
            await report_worker_heartbeat(state, session_factory)
        return True


async def main(session_factory: SessionFactory) -> None:
    state = WorkerState()
    presence = asyncio.create_task(worker_heartbeat(state, session_factory))
    try:
        while True:
            try:
                worked = await run_once(state, session_factory)
                if not worked:
                    await cleanup_staging(session_factory)
            except Exception as exc:
                # Tránh in thông tin xác thực nguồn từ thông báo ngoại lệ.
                logger.error("Ingestion worker cycle failed (%s)", type(exc).__name__)
                worked = False
            if not worked:
                await asyncio.sleep(2)
    finally:
        presence.cancel()
        await asyncio.gather(presence, return_exceptions=True)
