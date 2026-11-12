"""Lưu cấu hình nguồn trong cơ sở dữ liệu để dùng lại sau khi triển khai."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, Integer, String
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


class SilverProfileRevision(Base):
    """Append-only definitions; approvals never overwrite the rule that was reviewed."""

    __tablename__ = "silver_profile_revision"
    __table_args__ = {"schema": "app"}
    profile_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SilverDomainOwner(Base):
    """Explicit delegation. Revocation is retained; a new grant gets a new ID."""

    __tablename__ = "silver_domain_owner"
    __table_args__ = {"schema": "app"}
    grant_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    domain: Mapped[str] = mapped_column(String(63), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False)
    granted_by: Mapped[str] = mapped_column(String(100), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str] = mapped_column(String(2000), nullable=False)
    revoked_by: Mapped[str | None] = mapped_column(String(100))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revocation_reason: Mapped[str | None] = mapped_column(String(2000))


class SilverProfileApproval(Base):
    """One approval per revision; a revoked revision must be resubmitted as a new revision."""

    __tablename__ = "silver_profile_approval"
    __table_args__ = (
        ForeignKeyConstraint(
            ["profile_id", "revision"],
            ["app.silver_profile_revision.profile_id", "app.silver_profile_revision.revision"],
        ),
        {"schema": "app"},
    )
    profile_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    grant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("app.silver_domain_owner.grant_id")
    )
    approved_by: Mapped[str] = mapped_column(String(100), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence: Mapped[str] = mapped_column(String(2000), nullable=False)
    allow_partial_publication: Mapped[bool] = mapped_column(nullable=False)
    revoked_by: Mapped[str | None] = mapped_column(String(100))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revocation_reason: Mapped[str | None] = mapped_column(String(2000))
