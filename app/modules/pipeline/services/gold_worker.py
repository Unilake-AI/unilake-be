"""Bộ xử lý Gold độc lập; khi tạm dừng, các việc đã nhận vẫn còn trong PostgreSQL."""

from __future__ import annotations

import logging
from pathlib import Path
from threading import Event, Thread
from typing import TYPE_CHECKING

from app.core.config import settings
from app.modules.pipeline.repositories.gold import GoldRepository
from app.modules.pipeline.repositories.silver_storage import MinioSilverStore
from app.modules.pipeline.services.gold_pipeline import build_gold
from app.modules.pipeline.services.silver_worker import classify_failure
from app.providers import gold_writer_client

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)
HEALTH_FILE = Path("/tmp/unilake_gold_worker_heartbeat")


def run_once(engine: Engine, worker_id: str) -> bool:
    repository = GoldRepository(engine)
    repository.heartbeat(worker_id)
    HEALTH_FILE.touch()
    job = repository.claim(worker_id)
    if job is None:
        return False
    stop = Event()

    def pulse() -> None:
        while not stop.wait(10):
            try:
                if not repository.heartbeat(worker_id, job):
                    return
                HEALTH_FILE.touch()
            except Exception as exc:
                logger.warning("Chưa gia hạn được lượt Gold (%s)", type(exc).__name__)

    thread = Thread(target=pulse, daemon=True)
    thread.start()
    try:
        client = gold_writer_client()
        build_gold(
            job,
            MinioSilverStore(client, settings.minio_bucket_silver),
            MinioSilverStore(client, settings.minio_bucket_gold),
            repository,
        )
    except Exception as exc:
        # Không đưa chi tiết kết nối hoặc dữ liệu nguồn vào thông báo cho người dùng.
        code, retryable = classify_failure(exc)
        if isinstance(exc, ValueError) and str(exc).startswith("GOLD_"):
            code, retryable = str(exc), False
        else:
            code = code.replace("AUTO_PIPELINE_", "GOLD_")
        repository.fail(job, code, retryable)
        logger.warning("Lượt Gold %s chưa hoàn tất: %s", job["job_id"], code)
    finally:
        stop.set()
        thread.join(timeout=5)
    return True


def main(engine: Engine, worker_id: str) -> None:
    stop = Event()
    while not stop.is_set():
        try:
            run_once(engine, worker_id)
        except Exception as exc:
            logger.warning("Chưa nhận được việc Gold (%s)", type(exc).__name__)
        stop.wait(settings.gold_poll_seconds)
