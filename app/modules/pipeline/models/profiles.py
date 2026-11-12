"""Lưu cấu hình nguồn trong cơ sở dữ liệu để dùng lại sau khi triển khai."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base


class SilverSourceProfile(Base):
    __tablename__ = "silver_source_profile"
    __table_args__ = {"schema": "app"}

    profile_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_by: Mapped[str] = mapped_column(String(100), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SilverActivation(Base):
    """Ghi lại người lựa chọn phiên bản Silver và phiên bản đã được thay thế."""

    __tablename__ = "silver_activation"
    __table_args__ = {"schema": "app"}

    activation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scope: Mapped[str] = mapped_column(String(120), nullable=False)
    snapshot_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("app.silver_snapshot.snapshot_id")
    )
    previous_snapshot_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    activated_by: Mapped[str] = mapped_column(String(100), nullable=False)
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
