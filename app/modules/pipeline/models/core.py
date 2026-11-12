"""Các mô hình SQLAlchemy được xuất cho mô-đun pipeline."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    column,
    table,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base
from app.modules.pipeline.models.identity import (
    CanonicalIdentity,
    CrosswalkRelease,
    IdentityCrosswalk,
)
from app.modules.pipeline.models.silver import SilverHead, SilverRun, SilverSnapshot

__all__ = [
    "CLEANING_RUNS",
    "DATASET_CATALOG",
    "PIPELINE_LINEAGE",
    "QUARANTINE_RECORDS",
    "Base",
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


CLEANING_RUNS = Table(
    "cleaning_run",
    Base.metadata,
    Column("run_id", String(36), primary_key=True),
    Column(
        "ingestion_job_id",
        BigInteger,
        ForeignKey("app.ingestion_job.ingestion_job_id"),
        nullable=False,
    ),
    Column("dataset_id", BigInteger, ForeignKey("app.dataset.dataset_id"), nullable=False),
    Column("domain", String(50), nullable=False),
    Column("entity", String(100), nullable=False),
    Column("requested_by", String(100), nullable=False),
    Column("session", String(40), nullable=False),
    Column("bronze_version", Text, nullable=False),
    Column("input_version", Text),
    Column("cleaning_status", String(20), nullable=False),
    Column("publication_status", String(30), nullable=False),
    Column("records_read", BigInteger),
    Column("records_passed", BigInteger),
    Column("records_quarantined", BigInteger),
    Column("records_collapsed", BigInteger),
    Column("records_published", BigInteger, nullable=False),
    Column("silver_snapshot_id", String(36)),
    Column("report_ref", JSON),
    Column("quarantine_source", JSON),
    Column("error_code", String(80)),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True)),
    schema="app",
)


QUARANTINE_RECORDS = Table(
    "quarantine_record",
    Base.metadata,
    Column("record_id", String(36), primary_key=True),
    Column("cleaning_run_id", String(36), ForeignKey("app.cleaning_run.run_id"), nullable=False),
    Column("locator", Text, nullable=False),
    Column("record_position", BigInteger, nullable=False),
    Column("issues", JSON, nullable=False),
    Column("error_codes", JSONB, nullable=False),
    UniqueConstraint("cleaning_run_id", "locator"),
    schema="app",
)


# Tham chiếu ghi dữ liệu độc lập với metadata chứa mô hình ORM PipelineLineage.
PIPELINE_LINEAGE = Table(
    "pipeline_lineage",
    MetaData(),
    Column("lineage_id", BigInteger, primary_key=True, autoincrement=True),
    Column("source_layer", String(50), nullable=False),
    Column("source_name", Text, nullable=False),
    Column("target_layer", String(50), nullable=False),
    Column("target_name", Text, nullable=False),
    Column("metadata", JSON().with_variant(JSONB(), "postgresql"), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    schema="app",
)


# Chỉ chiếu các cột catalog cần cho tra cứu hồ sơ và phân quyền xem Bronze.
DATASET_CATALOG = table(
    "dataset",
    column("dataset_id", BigInteger),
    column("owner_department_id", BigInteger),
    column("dataset_code", String),
    column("status", String),
    schema="app",
)


DATA_QUALITY_ISSUES = Table(
    "data_quality_issue",
    Base.metadata,
    Column("issue_id", String(36), primary_key=True),
    Column("dataset_id", BigInteger, ForeignKey("app.dataset.dataset_id"), nullable=False),
    Column("department_id", BigInteger, ForeignKey("app.department.department_id"), nullable=False),
    Column("domain", String(50), nullable=False),
    Column("title", String(200), nullable=False),
    Column("description", Text, nullable=False),
    Column("status", String(30), nullable=False),
    Column("created_by", BigInteger, ForeignKey("app.app_user.app_user_id"), nullable=False),
    Column("assignee_user_id", BigInteger, ForeignKey("app.app_user.app_user_id")),
    Column("resolution_job_id", BigInteger, ForeignKey("app.ingestion_job.ingestion_job_id")),
    Column("verification_run_id", String(36), ForeignKey("app.cleaning_run.run_id")),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    schema="app",
)

DATA_QUALITY_ISSUE_RECORDS = Table(
    "data_quality_issue_record",
    Base.metadata,
    Column("issue_id", String(36), ForeignKey("app.data_quality_issue.issue_id"), primary_key=True),
    Column(
        "record_id", String(36), ForeignKey("app.quarantine_record.record_id"), primary_key=True
    ),
    schema="app",
)

DATA_QUALITY_ISSUE_EVENTS = Table(
    "data_quality_issue_event",
    Base.metadata,
    Column("event_id", BigInteger, Identity(always=True), primary_key=True),
    Column("issue_id", String(36), ForeignKey("app.data_quality_issue.issue_id"), nullable=False),
    Column("actor_id", BigInteger, ForeignKey("app.app_user.app_user_id"), nullable=False),
    Column("event_type", String(30), nullable=False),
    Column("from_status", String(30)),
    Column("to_status", String(30), nullable=False),
    Column("details", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    schema="app",
)
