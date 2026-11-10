"""Các mô hình SQLAlchemy cho mô-đun ingestion."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base

__all__ = ["IngestionJob"]


class IngestionJob(Base):
    """Bản ghi thực thi việc nạp dữ liệu từ nguồn đã đăng ký."""

    __tablename__ = "ingestion_job"
    __table_args__ = (
        Index("ix_ingestion_job_data_source_id", "data_source_id"),
        Index("ix_ingestion_job_dataset_id", "dataset_id"),
        Index("ix_ingestion_job_target_layer_id", "target_layer_id"),
        Index("ix_ingestion_job_status", "status"),
        {"schema": "app"},
    )

    ingestion_job_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    data_source_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.data_source.data_source_id"), nullable=False
    )
    dataset_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.dataset.dataset_id"), nullable=False
    )
    target_layer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.data_layer.data_layer_id"), nullable=False
    )
    job_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    rows_read: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    rows_written: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    rows_rejected: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_table: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    requested_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    stage: Mapped[str | None] = mapped_column(String(30), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    validation_input: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    validation_report: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    upload_input: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    bronze_output: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    root_job_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("app.ingestion_job.ingestion_job_id"), nullable=True
    )
    attempt_number: Mapped[int] = mapped_column(nullable=False, default=1)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    retryable: Mapped[bool] = mapped_column(nullable=False, default=False)


class IngestionWorkerHeartbeat(Base):
    """Dấu hiệu worker còn kết nối được hàng đợi, kể cả lúc không có job."""

    __tablename__ = "ingestion_worker_heartbeat"
    __table_args__ = {"schema": "app"}

    worker_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    current_job_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("app.ingestion_job.ingestion_job_id"), nullable=True
    )
    last_heartbeat_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
