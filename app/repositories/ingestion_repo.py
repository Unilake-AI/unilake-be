"""Hàng đợi PostgreSQL bền vững. Mọi thay đổi đều commit trước I/O bên ngoài."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.modules.datasources.models import DataLayer, Dataset, DataSource, SourceSystem
from app.modules.ingestion.models import IngestionJob
from app.repositories.datasource_repo import DataSourceRepository

if TYPE_CHECKING:
    from app.modules.ingestion.validation import ValidationReport

JOB_TYPE = "EXECUTION_V1"
LEASE_SECONDS = 120
SourceContext = tuple[DataSource, Dataset, DataLayer | None, SourceSystem | None]


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class IngestionRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.sources = DataSourceRepository(db)

    async def context(
        self, source_id: int, dataset_id: int, *, lock: bool = False
    ) -> SourceContext:
        query = select(DataSource).where(DataSource.data_source_id == source_id)
        if lock:
            query = query.with_for_update()
        source = await self.db.scalar(query)
        dataset = await self.db.get(Dataset, dataset_id)
        if source is None or dataset is None:
            raise AppException(
                "Source hoặc dataset không tồn tại", code="INGESTION_NOT_FOUND", status_code=404
            )
        layer = await self.db.get(DataLayer, dataset.data_layer_id)
        system = await self.db.get(SourceSystem, source.source_system_id)
        return source, dataset, layer, system

    async def active(self, source_id: int) -> IngestionJob | None:
        result = await self.db.scalars(
            select(IngestionJob).where(
                IngestionJob.data_source_id == source_id,
                IngestionJob.job_type == JOB_TYPE,
                IngestionJob.status.in_(("QUEUED", "RUNNING")),
            )
        )
        return result.first()

    async def create(self, **values: Any) -> IngestionJob:
        job = IngestionJob(job_type=JOB_TYPE, created_at=utcnow(), **values)
        self.db.add(job)
        await self.db.flush()
        if job.status == "QUEUED":
            await self.db.execute(
                update(DataSource)
                .where(DataSource.data_source_id == job.data_source_id)
                .values(last_ingestion_status="QUEUED")
            )
        await self.db.commit()
        return job

    async def get(self, job_id: int) -> IngestionJob:
        job = await self.db.get(IngestionJob, job_id)
        if job is None or job.job_type != JOB_TYPE:
            raise AppException("Job không tồn tại", code="INGESTION_JOB_NOT_FOUND", status_code=404)
        return job

    async def list(self, source_id: int | None, limit: int, offset: int) -> list[IngestionJob]:
        query = select(IngestionJob).where(IngestionJob.job_type == JOB_TYPE)
        if source_id is not None:
            query = query.where(IngestionJob.data_source_id == source_id)
        result = await self.db.scalars(
            query.order_by(IngestionJob.ingestion_job_id.desc()).limit(limit).offset(offset)
        )
        return list(result)

    async def claim(self) -> IngestionJob | None:
        job = await self.db.scalar(
            select(IngestionJob)
            .where(IngestionJob.job_type == JOB_TYPE, IngestionJob.status == "QUEUED")
            .order_by(IngestionJob.ingestion_job_id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is not None:
            job.status, job.stage, job.started_at = "RUNNING", "READING", utcnow()
            job.lease_expires_at = utcnow() + timedelta(seconds=LEASE_SECONDS)
            await self.db.execute(
                update(DataSource)
                .where(DataSource.data_source_id == job.data_source_id)
                .values(last_ingestion_status="RUNNING")
            )
        await self.db.commit()
        return job

    async def heartbeat(self, job_id: int) -> bool:
        result = await self.db.execute(
            update(IngestionJob)
            .where(
                IngestionJob.ingestion_job_id == job_id,
                IngestionJob.status == "RUNNING",
                IngestionJob.lease_expires_at > utcnow(),
            )
            .values(lease_expires_at=utcnow() + timedelta(seconds=LEASE_SECONDS))
            .returning(IngestionJob.ingestion_job_id)
        )
        updated_id = result.scalar_one_or_none()
        await self.db.commit()
        return updated_id is not None

    async def stage(self, job_id: int, stage: str, rows: int) -> None:
        result = await self.db.execute(
            update(IngestionJob)
            .where(
                IngestionJob.ingestion_job_id == job_id,
                IngestionJob.status == "RUNNING",
                IngestionJob.lease_expires_at > utcnow(),
            )
            .values(stage=stage, rows_read=rows)
            .returning(IngestionJob.ingestion_job_id)
        )
        updated_id = result.scalar_one_or_none()
        await self.db.commit()
        if updated_id is None:
            raise RuntimeError("INGESTION_LEASE_LOST")

    async def save_validation(self, job_id: int, report: ValidationReport) -> None:
        """Commit report trước upload; lease mất thì dừng, không công bố kết quả cũ."""
        result = await self.db.execute(
            update(IngestionJob)
            .where(
                IngestionJob.ingestion_job_id == job_id,
                IngestionJob.status == "RUNNING",
                IngestionJob.lease_expires_at > utcnow(),
            )
            .values(
                validation_report=report.model_dump(mode="json"), rows_read=report.records_scanned
            )
            .returning(IngestionJob.ingestion_job_id)
        )
        updated_id = result.scalar_one_or_none()
        await self.db.commit()
        if updated_id is None:
            raise RuntimeError("INGESTION_LEASE_LOST")

    async def finish(
        self, job_id: int, *, output: dict[str, Any] | None = None, error: str | None = None
    ) -> bool:
        job = await self.db.scalar(
            select(IngestionJob)
            .where(
                IngestionJob.ingestion_job_id == job_id,
                IngestionJob.status == "RUNNING",
                IngestionJob.lease_expires_at > utcnow(),
            )
            .with_for_update()
        )
        if job is None:
            await self.db.rollback()
            return False
        job.status = "FAILED" if error else "SUCCESS"
        job.stage = "FAILED" if error else "COMPLETED"
        job.error_message, job.output, job.finished_at = error, output, utcnow()
        job.rows_written = 0 if error else job.rows_read
        job.rows_rejected = 0
        job.lease_expires_at = None
        values: dict[str, Any] = {"last_ingestion_status": job.status}
        if not error:
            values.update(
                last_ingested_at=job.finished_at, ingestion_count=DataSource.ingestion_count + 1
            )
        await self.db.execute(
            update(DataSource)
            .where(DataSource.data_source_id == job.data_source_id)
            .values(**values)
        )
        await self.db.commit()
        return True

    async def recover_expired(self) -> int:
        jobs = list(
            await self.db.scalars(
                select(IngestionJob)
                .where(
                    IngestionJob.job_type == JOB_TYPE,
                    IngestionJob.status == "RUNNING",
                    IngestionJob.lease_expires_at <= utcnow(),
                )
                .with_for_update(skip_locked=True)
            )
        )
        for job in jobs:
            job.status, job.stage = "FAILED", "FAILED"
            job.error_message = "WORKER_LEASE_EXPIRED"
            job.finished_at, job.lease_expires_at = utcnow(), None
            job.rows_written = 0
            await self.db.execute(
                update(DataSource)
                .where(DataSource.data_source_id == job.data_source_id)
                .values(last_ingestion_status="FAILED")
            )
        await self.db.commit()
        return len(jobs)
