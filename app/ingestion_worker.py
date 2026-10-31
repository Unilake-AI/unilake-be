"""Chạy bằng python -m app.ingestion_worker. PostgreSQL là hàng đợi bền vững."""

from __future__ import annotations

import asyncio
import logging

from app.bronze_capture import BronzeCapture, store_bronze
from app.infra.db.session import async_session_factory
from app.modules.ingestion.execution import IngestionExecutionService
from app.repositories.ingestion_repo import IngestionRepository

logger = logging.getLogger(__name__)


async def heartbeat(job_id: int) -> None:
    while True:
        await asyncio.sleep(15)
        async with async_session_factory() as session:
            if not await IngestionRepository(session).heartbeat(job_id):
                raise RuntimeError("INGESTION_LEASE_LOST")


async def run_once() -> bool:
    async with async_session_factory() as session:
        repo = IngestionRepository(session)
        await repo.recover_expired()
        job = await repo.claim()
        if job is None:
            return False
        pulse = asyncio.create_task(heartbeat(job.ingestion_job_id))
        execution = asyncio.create_task(
            IngestionExecutionService(repo).execute(job, BronzeCapture, store_bronze)
        )
        try:
            done, _ = await asyncio.wait({pulse, execution}, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                await task
        finally:
            for task in (pulse, execution):
                task.cancel()
            await asyncio.gather(pulse, execution, return_exceptions=True)
        return True


async def main() -> None:
    while True:
        try:
            worked = await run_once()
        except Exception as exc:
            # Tránh in thông tin xác thực nguồn từ thông báo ngoại lệ.
            logger.error("Ingestion worker cycle failed (%s)", type(exc).__name__)
            worked = False
        if not worked:
            await asyncio.sleep(2)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
