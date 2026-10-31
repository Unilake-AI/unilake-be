"""Các mô hình SQLAlchemy được xuất cho mô-đun pipeline."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base
from app.modules.pipeline.identity_models import (
    CanonicalIdentity,
    CrosswalkRelease,
    IdentityCrosswalk,
)
from app.modules.pipeline.silver_models import SilverHead, SilverRun, SilverSnapshot

__all__ = [
    "CanonicalIdentity",
    "CrosswalkRelease",
    "DataProcessingRun",
    "IdentityCrosswalk",
    "SilverHead",
    "SilverRun",
    "SilverSnapshot",
    "TransformationStep",
]


class TransformationStep(Base):
    """Một bước biến đổi được thực thi giữa dataset nguồn và đích."""

    __tablename__ = "transformation_step"
    __table_args__ = (
        Index("ix_transformation_step_ingestion_job_id", "ingestion_job_id"),
        Index("ix_transformation_step_source_dataset_id", "source_dataset_id"),
        Index("ix_transformation_step_target_dataset_id", "target_dataset_id"),
        Index("ix_transformation_step_status", "status"),
        {"schema": "app"},
    )

    transformation_step_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    ingestion_job_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("app.ingestion_job.ingestion_job_id"), nullable=True
    )
    source_dataset_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.dataset.dataset_id"), nullable=False
    )
    target_dataset_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.dataset.dataset_id"), nullable=False
    )
    step_name: Mapped[str] = mapped_column(String(150), nullable=False)
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    transformation_type: Mapped[str] = mapped_column(String(50), nullable=False)
    transformation_expression: Mapped[str | None] = mapped_column(Text, nullable=True)
    rows_input: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    rows_output: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class DataProcessingRun(Base):
    """Metadata lần chạy pipeline xuyên suốt ingestion và biến đổi dữ liệu."""

    __tablename__ = "data_processing_run"
    __table_args__ = (
        Index("ix_data_processing_run_triggered_by", "triggered_by"),
        Index("ix_data_processing_run_status", "status"),
        Index("ix_data_processing_run_run_type", "run_type"),
        {"schema": "app"},
    )

    run_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    run_type: Mapped[str] = mapped_column(String(50), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    triggered_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("app.app_user.app_user_id"), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
