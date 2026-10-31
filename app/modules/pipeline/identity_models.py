"""Lưu trữ đối chiếu định danh chỉ thêm mới; cài schema tường minh, không cài khi import."""

from __future__ import annotations

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Identity, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base


class CanonicalIdentity(Base):
    __tablename__ = "canonical_identity"
    __table_args__ = (CheckConstraint("canonical_id > 0"), {"schema": "app"})
    canonical_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        Identity(),
        primary_key=True,
        autoincrement=True,
    )
    entity: Mapped[str] = mapped_column(String(80), nullable=False)


class IdentityCrosswalk(Base):
    __tablename__ = "identity_crosswalk"
    __table_args__ = ({"schema": "app"},)
    namespace: Mapped[str] = mapped_column(String(100), primary_key=True)
    entity: Mapped[str] = mapped_column(String(80), primary_key=True)
    source_key: Mapped[str] = mapped_column(String(1024), primary_key=True)
    canonical_id: Mapped[int] = mapped_column(
        ForeignKey("app.canonical_identity.canonical_id"), nullable=False
    )
    evidence: Mapped[str] = mapped_column(Text, nullable=False)
    valid_from: Mapped[str] = mapped_column(String(40), nullable=False)
    valid_to: Mapped[str | None] = mapped_column(String(40), nullable=True)


class CrosswalkRelease(Base):
    __tablename__ = "crosswalk_release"
    __table_args__ = ({"schema": "app"},)
    version: Mapped[str] = mapped_column(String(100), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
