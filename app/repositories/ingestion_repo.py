"""Hàng đợi PostgreSQL bền vững. Mọi thay đổi đều commit trước I/O bên ngoài."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import case, func, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppException
from app.modules.datasources.models import DataLayer, Dataset, DataSource, SourceSystem
from app.modules.ingestion.models import IngestionJob, IngestionWorkerHeartbeat
from app.modules.pipeline.models import PIPELINE_LINEAGE
from app.repositories.datasource_repo import DataSourceRepository

if TYPE_CHECKING:
    from app.modules.ingestion.validation import ValidationReport

JOB_TYPE = "EXECUTION_V1"
UPLOAD_JOB_TYPE = "UPLOAD_V1"

# Cả hai cách nạp dữ liệu dùng chung việc nhận job, gia hạn và tra cứu kết quả.
SUPPORTED_JOB_TYPES = (JOB_TYPE, UPLOAD_JOB_TYPE)
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
                IngestionJob.job_type.in_(SUPPORTED_JOB_TYPES),
                IngestionJob.status.in_(("QUEUED", "RETRY_WAIT", "RUNNING")),
            )
        )
        return result.first()

    async def create(self, **values: Any) -> IngestionJob:
        job = IngestionJob(created_at=utcnow(), **{"job_type": JOB_TYPE, **values})
        self.db.add(job)
        await self.db.flush()
        # Job đầu tiên là gốc của chính nó; các lần chạy lại giữ chung mã gốc này.
        if job.root_job_id is None:
            job.root_job_id = job.ingestion_job_id
        if job.status == "QUEUED":
            await self.db.execute(
                update(DataSource)
                .where(DataSource.data_source_id == job.data_source_id)
                .values(last_ingestion_status="QUEUED")
            )
        await self.db.commit()
        return job

    async def heartbeat_worker(self, worker_id: str, current_job_id: int | None) -> None:
        """Ghi dấu hiệu worker còn hoạt động, kể cả khi hàng đợi đang trống."""
        worker = await self.db.get(IngestionWorkerHeartbeat, worker_id)
        if worker is None:
            self.db.add(
                IngestionWorkerHeartbeat(
                    worker_id=worker_id,
                    current_job_id=current_job_id,
                    last_heartbeat_at=utcnow(),
                )
            )
        else:
            worker.current_job_id = current_job_id
            worker.last_heartbeat_at = utcnow()
        await self.db.commit()

    async def stats(self, domains: frozenset[str] | None = None) -> dict[str, int]:
        """Đếm trạng thái job trong phạm vi dữ liệu người gọi được phép xem."""
        query = select(
            func.count(IngestionJob.ingestion_job_id).label("total"),
            func.count(IngestionJob.ingestion_job_id)
            .filter(IngestionJob.status == "QUEUED")
            .label("queued"),
            func.count(IngestionJob.ingestion_job_id)
            .filter(IngestionJob.status == "RUNNING")
            .label("running"),
            func.count(IngestionJob.ingestion_job_id)
            .filter(IngestionJob.status == "SUCCESS")
            .label("success"),
            func.count(IngestionJob.ingestion_job_id)
            .filter(IngestionJob.status.in_(("FAILED", "REJECTED")))
            .label("failed"),
        ).where(IngestionJob.job_type.in_(SUPPORTED_JOB_TYPES))
        if domains is not None:
            query = query.join(Dataset, Dataset.dataset_id == IngestionJob.dataset_id).where(
                Dataset.domain.in_(domains)
            )
        row = (await self.db.execute(query)).one()
        return {
            name: int(getattr(row, name) or 0)
            for name in ("total", "queued", "running", "success", "failed")
        }

    async def metrics(self, domains: frozenset[str] | None = None) -> dict[str, float | None]:
        """Tính số liệu vận hành từ thời điểm job đã lưu, không dùng dữ liệu ở giao diện."""
        wait = func.extract("epoch", IngestionJob.started_at - IngestionJob.created_at)
        processing = func.extract("epoch", IngestionJob.finished_at - IngestionJob.started_at)
        now = func.now()
        oldest = func.extract(
            "epoch", now - func.min(IngestionJob.created_at).filter(IngestionJob.status == "QUEUED")
        )
        query = select(
            func.avg(wait).filter(IngestionJob.started_at.is_not(None)).label("wait"),
            func.avg(processing)
            .filter(IngestionJob.finished_at.is_not(None), IngestionJob.started_at.is_not(None))
            .label("processing"),
            (100.0 * func.avg(case((IngestionJob.status == "FAILED", 1), else_=0))).label(
                "failure"
            ),
            oldest.label("oldest"),
        ).where(IngestionJob.job_type.in_(SUPPORTED_JOB_TYPES))
        if domains is not None:
            query = query.join(Dataset, Dataset.dataset_id == IngestionJob.dataset_id).where(
                Dataset.domain.in_(domains)
            )
        row = (await self.db.execute(query)).one()
        return {
            "average_wait_seconds": float(row.wait) if row.wait is not None else None,
            "average_processing_seconds": float(row.processing)
            if row.processing is not None
            else None,
            "failure_rate_percent": float(row.failure) if row.failure is not None else None,
            "oldest_queued_seconds": float(row.oldest) if row.oldest is not None else None,
        }

    async def workers(self) -> list[IngestionWorkerHeartbeat]:
        return list(
            await self.db.scalars(
                select(IngestionWorkerHeartbeat).order_by(IngestionWorkerHeartbeat.worker_id)
            )
        )

    async def expired_staging_inputs(self, before: datetime) -> list[dict[str, Any]]:
        """Chỉ trả file upload khi mọi lần trong chuỗi đã kết thúc và hết hạn lưu."""
        jobs = list(
            await self.db.scalars(
                select(IngestionJob).where(
                    IngestionJob.job_type == UPLOAD_JOB_TYPE,
                    IngestionJob.upload_input.is_not(None),
                )
            )
        )
        grouped: dict[int, list[IngestionJob]] = {}
        for job in jobs:
            grouped.setdefault(job.root_job_id or job.ingestion_job_id, []).append(job)
        inputs: list[dict[str, Any]] = []
        terminal = {"SUCCESS", "FAILED", "REJECTED"}
        for chain in grouped.values():
            if not all(job.status in terminal for job in chain):
                continue
            timestamps = [job.finished_at or job.created_at for job in chain]
            known_timestamps = [timestamp for timestamp in timestamps if timestamp is not None]
            # Thiếu thời điểm của một lần chạy thì giữ file để tránh xóa quá sớm.
            if len(known_timestamps) != len(chain):
                continue
            latest = max(known_timestamps)
            if latest > before:
                continue
            evidence = chain[0].upload_input
            if isinstance(evidence, dict):
                inputs.append(evidence)
        return inputs

    async def retry(self, job_id: int, requested_by: str) -> IngestionJob:
        """Tạo lần chạy lại từ file upload đã ghim; không sửa lịch sử job cũ."""
        original = await self.db.scalar(
            select(IngestionJob).where(IngestionJob.ingestion_job_id == job_id).with_for_update()
        )
        if original is None or original.job_type not in SUPPORTED_JOB_TYPES:
            raise AppException("Job không tồn tại", code="INGESTION_JOB_NOT_FOUND", status_code=404)
        if original.status != "FAILED":
            raise AppException(
                "Chỉ có thể chạy lại job đã thất bại",
                code="INGESTION_RETRY_STATUS_INVALID",
                status_code=409,
            )
        # Nguồn upload đã giữ phiên bản và checksum. Nguồn khác chưa có cơ chế ghim đủ an toàn.
        if original.job_type != UPLOAD_JOB_TYPE or not original.upload_input:
            raise AppException(
                "Chỉ có thể chạy lại file đã tải lên ở giai đoạn này",
                code="INGESTION_RETRY_SOURCE_NOT_PINNED",
                status_code=422,
            )
        root_id = original.root_job_id or original.ingestion_job_id
        root = await self.db.scalar(
            select(IngestionJob).where(IngestionJob.ingestion_job_id == root_id).with_for_update()
        )
        if root is None:
            raise AppException(
                "Không tìm thấy chuỗi job", code="INGESTION_RETRY_ROOT_NOT_FOUND", status_code=409
            )
        existing_bronze = await self.db.scalar(
            select(IngestionJob.bronze_output)
            .where(
                IngestionJob.root_job_id == root_id,
                IngestionJob.bronze_output.is_not(None),
            )
            .order_by(IngestionJob.ingestion_job_id.desc())
            .limit(1)
        )
        active = await self.db.scalar(
            select(IngestionJob.ingestion_job_id)
            .where(
                IngestionJob.root_job_id == root_id,
                IngestionJob.status.in_(("QUEUED", "RUNNING")),
            )
            .limit(1)
        )
        if active is not None:
            raise AppException(
                "Chuỗi job này đang được xử lý",
                code="INGESTION_RETRY_ALREADY_ACTIVE",
                status_code=409,
            )
        last_attempt = await self.db.scalar(
            select(func.max(IngestionJob.attempt_number)).where(IngestionJob.root_job_id == root_id)
        )
        return await self.create(
            job_type=original.job_type,
            data_source_id=original.data_source_id,
            dataset_id=original.dataset_id,
            target_layer_id=original.target_layer_id,
            source_table=original.source_table,
            requested_by=requested_by[:100],
            status="QUEUED",
            stage="QUEUED",
            validation_input=original.validation_input,
            upload_input=original.upload_input,
            bronze_output=existing_bronze,
            root_job_id=root_id,
            attempt_number=int(last_attempt or 0) + 1,
        )

    async def checkpoint_bronze(self, job_id: int, output: dict[str, Any]) -> None:
        """Lưu dấu vết Bronze đã xác minh trước khi chốt job thành công."""
        result = await self.db.execute(
            update(IngestionJob)
            .where(
                IngestionJob.ingestion_job_id == job_id,
                IngestionJob.status == "RUNNING",
                IngestionJob.lease_expires_at > utcnow(),
            )
            .values(bronze_output=output)
            .returning(IngestionJob.ingestion_job_id)
        )
        if result.scalar_one_or_none() is None:
            await self.db.rollback()
            raise RuntimeError("INGESTION_LEASE_LOST")
        await self.db.commit()

    async def schedule_retry(self, job_id: int, error_code: str) -> IngestionJob | None:
        """Tạo lần chạy lại có chờ khi lỗi hạ tầng tạm thời và còn lượt hợp lệ."""
        job = await self.db.scalar(
            select(IngestionJob).where(IngestionJob.ingestion_job_id == job_id).with_for_update()
        )
        if job is None or job.status != "FAILED" or not job.retryable:
            return None
        root_id = job.root_job_id or job.ingestion_job_id
        if job.attempt_number >= settings.ingestion_retry_max_attempts:
            return None
        delay = (
            settings.ingestion_retry_first_delay_seconds
            if job.attempt_number == 1
            else settings.ingestion_retry_second_delay_seconds
        )
        return await self.create(
            job_type=job.job_type,
            data_source_id=job.data_source_id,
            dataset_id=job.dataset_id,
            target_layer_id=job.target_layer_id,
            source_table=job.source_table,
            requested_by="system-retry",
            status="RETRY_WAIT",
            stage="RETRY_WAIT",
            validation_input=job.validation_input,
            upload_input=job.upload_input,
            bronze_output=job.bronze_output,
            root_job_id=root_id,
            attempt_number=job.attempt_number + 1,
            next_attempt_at=utcnow() + timedelta(seconds=delay),
            retryable=False,
            error_message=error_code,
        )

    async def attempts(
        self, job_id: int, domains: frozenset[str] | None = None
    ) -> list[IngestionJob]:
        job = await self.get(job_id, domains)
        root_id = job.root_job_id or job.ingestion_job_id
        query = select(IngestionJob).where(IngestionJob.root_job_id == root_id)
        result = await self.db.scalars(query.order_by(IngestionJob.attempt_number))
        return list(result)

    async def get(self, job_id: int, domains: frozenset[str] | None = None) -> IngestionJob:
        query = select(IngestionJob).where(IngestionJob.ingestion_job_id == job_id)
        if domains is not None:
            query = query.join(Dataset, Dataset.dataset_id == IngestionJob.dataset_id).where(
                Dataset.domain.in_(domains)
            )
        job = await self.db.scalar(query)
        if job is None or job.job_type not in SUPPORTED_JOB_TYPES:
            raise AppException("Job không tồn tại", code="INGESTION_JOB_NOT_FOUND", status_code=404)
        return job

    async def list(
        self, source_id: int | None, limit: int, offset: int, domains: frozenset[str] | None = None
    ) -> list[IngestionJob]:
        query = select(IngestionJob).where(IngestionJob.job_type.in_(SUPPORTED_JOB_TYPES))
        if domains is not None:
            query = query.join(Dataset, Dataset.dataset_id == IngestionJob.dataset_id).where(
                Dataset.domain.in_(domains)
            )
        if source_id is not None:
            query = query.where(IngestionJob.data_source_id == source_id)
        result = await self.db.scalars(
            query.order_by(IngestionJob.ingestion_job_id.desc()).limit(limit).offset(offset)
        )
        return list(result)

    async def claim(self) -> IngestionJob | None:
        job = await self.db.scalar(
            select(IngestionJob)
            .where(
                IngestionJob.job_type.in_(SUPPORTED_JOB_TYPES),
                or_(
                    IngestionJob.status == "QUEUED",
                    (IngestionJob.status == "RETRY_WAIT")
                    & (IngestionJob.next_attempt_at <= utcnow()),
                ),
            )
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
        self,
        job_id: int,
        *,
        output: dict[str, Any] | None = None,
        error: str | None = None,
        retryable: bool = False,
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
        job.retryable = retryable if error else False
        job.rows_written = 0 if error else job.rows_read
        job.rows_rejected = 0
        job.lease_expires_at = None
        values: dict[str, Any] = {"last_ingestion_status": job.status}
        if not error:
            values.update(
                last_ingested_at=job.finished_at, ingestion_count=DataSource.ingestion_count + 1
            )
            if output and output.get("uri"):
                # Lưu nguồn gốc cùng kết quả; nếu chưa lưu được thì chưa báo nạp thành công.
                await self.record_lineage(
                    source_layer="SOURCE",
                    source_name=job.source_table or f"source:{job.data_source_id}",
                    target_layer="BRONZE",
                    target_name=str(output["uri"]),
                    metadata={
                        "ingestion_job_id": job.ingestion_job_id,
                        "data_source_id": job.data_source_id,
                        "dataset_id": job.dataset_id,
                        "domain": output.get("domain"),
                        "records_read": job.rows_read,
                        "records_passed": job.rows_written,
                        "records_rejected": job.rows_rejected,
                        "size_bytes": output.get("size_bytes"),
                        "sha256": output.get("sha256"),
                        "version_id": output.get("version_id"),
                    },
                )
        await self.db.execute(
            update(DataSource)
            .where(DataSource.data_source_id == job.data_source_id)
            .values(**values)
        )
        await self.db.commit()
        return True

    async def record_lineage(
        self,
        *,
        source_layer: str,
        source_name: str,
        target_layer: str,
        target_name: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        stmt = insert(PIPELINE_LINEAGE).values(
            source_layer=source_layer,
            source_name=source_name,
            target_layer=target_layer,
            target_name=target_name,
            metadata=metadata or {},
            created_at=datetime.now(UTC),
        )
        await self.db.execute(stmt)

    async def recover_expired(self) -> int:
        jobs = list(
            await self.db.scalars(
                select(IngestionJob)
                .where(
                    IngestionJob.job_type.in_(SUPPORTED_JOB_TYPES),
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
