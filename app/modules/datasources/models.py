"""Các mô hình SQLAlchemy cho mô-đun nguồn dữ liệu."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infra.db.base import Base

__all__ = ["DataLayer", "DataSource", "Dataset", "SourceSystem"]


class SourceSystem(Base):
    """Hệ thống nguồn nghiệp vụ sở hữu một hoặc nhiều nguồn dữ liệu."""

    __tablename__ = "source_system"
    __table_args__ = (
        Index("ix_source_system_owner_department_id", "owner_department_id"),
        Index("ix_source_system_source_type", "source_type"),
        Index("ix_source_system_is_active", "is_active"),
        {"schema": "app"},
    )

    source_system_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    source_code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    source_name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_department_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("app.department.department_id"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class DataSource(Base):
    """Cấu hình nguồn dữ liệu được các luồng ingestion sử dụng."""

    __tablename__ = "data_source"
    __table_args__ = (
        Index("ix_data_source_source_system_id", "source_system_id"),
        Index("ix_data_source_created_by", "created_by"),
        Index("ix_data_source_source_type", "source_type"),
        Index("ix_data_source_is_active", "is_active"),
        {"schema": "app"},
    )

    data_source_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    source_system_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.source_system.source_system_id"), nullable=False
    )
    source_name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    connection_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    location: Mapped[str | None] = mapped_column(Text, nullable=True)
    configuration: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"), nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_ingested_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_ingestion_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    ingestion_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("app.app_user.app_user_id"), nullable=True
    )
    creator: Mapped[Any | None] = relationship("User", foreign_keys=[created_by])
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(nullable=True)


class DataLayer(Base):
    """Tham chiếu tầng lakehouse như Bronze, Silver và Gold."""

    __tablename__ = "data_layer"
    __table_args__ = ({"schema": "app"},)

    data_layer_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    layer_code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    layer_name: Mapped[str] = mapped_column(String(50), nullable=False)
    storage_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class Dataset(Base):
    """Dataset đã đăng ký trong một tầng dữ liệu."""

    __tablename__ = "dataset"
    __table_args__ = (
        Index("ix_dataset_source_system_id", "source_system_id"),
        Index("ix_dataset_data_layer_id", "data_layer_id"),
        Index("ix_dataset_owner_department_id", "owner_department_id"),
        Index("ix_dataset_domain", "domain"),
        Index("ix_dataset_status", "status"),
        {"schema": "app"},
    )

    dataset_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    dataset_code: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    dataset_name: Mapped[str] = mapped_column(String(200), nullable=False)
    domain: Mapped[str] = mapped_column(String(50), nullable=False)
    source_system_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("app.source_system.source_system_id"), nullable=True
    )
    data_layer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.data_layer.data_layer_id"), nullable=False
    )
    owner_department_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("app.department.department_id"), nullable=True
    )
    storage_format: Mapped[str | None] = mapped_column(String(50), nullable=True)
    location: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[str | None] = mapped_column(String(30), nullable=True)
    status: Mapped[str] = mapped_column(String(30), server_default=text("'ACTIVE'"), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(nullable=True)
