"""Các mô hình SQLAlchemy cho mô-đun quản trị dữ liệu."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base

__all__ = [
    "CatalogColumn",
    "CatalogTable",
    "DataLineage",
    "DataQualityRule",
    "DataQualityRun",
]


class CatalogTable(Base):
    __tablename__ = "catalog_table"
    __table_args__ = (
        UniqueConstraint("dataset_id", "table_name", name="uq_catalog_table"),
        Index("ix_catalog_table_dataset_id", "dataset_id"),
        {"schema": "app"},
    )

    catalog_table_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    dataset_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.dataset.dataset_id"), nullable=False
    )
    table_name: Mapped[str] = mapped_column(String(150), nullable=False)
    source_table_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner: Mapped[str | None] = mapped_column(String(150), nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class CatalogColumn(Base):
    __tablename__ = "catalog_column"
    __table_args__ = (
        UniqueConstraint("catalog_table_id", "column_name", name="uq_catalog_column"),
        Index("ix_catalog_column_catalog_table_id", "catalog_table_id"),
        Index("ix_catalog_column_data_type", "data_type"),
        {"schema": "app"},
    )

    catalog_column_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    catalog_table_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.catalog_table.catalog_table_id"), nullable=False
    )
    column_name: Mapped[str] = mapped_column(String(150), nullable=False)
    data_type: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_nullable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_primary_key: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), nullable=False
    )
    is_foreign_key: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), nullable=False
    )


class DataLineage(Base):
    __tablename__ = "data_lineage"
    __table_args__ = (
        Index("ix_data_lineage_transformation_step_id", "transformation_step_id"),
        Index("ix_data_lineage_source_column_id", "source_column_id"),
        Index("ix_data_lineage_target_column_id", "target_column_id"),
        {"schema": "app"},
    )

    data_lineage_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    transformation_step_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.transformation_step.transformation_step_id"), nullable=False
    )
    source_column_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.catalog_column.catalog_column_id"), nullable=False
    )
    target_column_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.catalog_column.catalog_column_id"), nullable=False
    )
    lineage_type: Mapped[str] = mapped_column(String(50), nullable=False)
    transformation_expression: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class DataQualityRule(Base):
    __tablename__ = "data_quality_rule"
    __table_args__ = (
        Index("ix_data_quality_rule_catalog_column_id", "catalog_column_id"),
        Index("ix_data_quality_rule_rule_type", "rule_type"),
        Index("ix_data_quality_rule_is_active", "is_active"),
        {"schema": "app"},
    )

    data_quality_rule_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    catalog_column_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.catalog_column.catalog_column_id"), nullable=False
    )
    rule_code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    rule_name: Mapped[str] = mapped_column(String(150), nullable=False)
    rule_type: Mapped[str] = mapped_column(String(50), nullable=False)
    rule_expression: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(20), server_default=text("'HIGH'"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)


class DataQualityRun(Base):
    __tablename__ = "data_quality_run"
    __table_args__ = (
        Index("ix_data_quality_run_data_quality_rule_id", "data_quality_rule_id"),
        Index("ix_data_quality_run_ingestion_job_id", "ingestion_job_id"),
        Index("ix_data_quality_run_execution_status", "execution_status"),
        {"schema": "app"},
    )

    data_quality_run_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    data_quality_rule_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.data_quality_rule.data_quality_rule_id"), nullable=False
    )
    ingestion_job_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("app.ingestion_job.ingestion_job_id"), nullable=True
    )
    run_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    total_records: Mapped[int] = mapped_column(BigInteger, nullable=False)
    passed_records: Mapped[int] = mapped_column(BigInteger, nullable=False)
    failed_records: Mapped[int] = mapped_column(BigInteger, nullable=False)
    pass_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    execution_status: Mapped[str] = mapped_column(String(30), nullable=False)
    failure_sample_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
