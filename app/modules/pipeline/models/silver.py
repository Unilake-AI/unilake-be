from __future__ import annotations

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base


class SilverRun(Base):
    __tablename__ = "silver_run"
    __table_args__ = ({"schema": "app"},)
    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scope: Mapped[str] = mapped_column(String(120), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    base_snapshot: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    manifest_ref: Mapped[str | None] = mapped_column(Text)
    issues: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'[]'"))


class SilverSnapshot(Base):
    __tablename__ = "silver_snapshot"
    __table_args__ = (
        UniqueConstraint("scope", "request_hash", name="uq_silver_snapshot_request"),
        {"schema": "app"},
    )
    snapshot_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("app.silver_run.run_id"), primary_key=True
    )
    scope: Mapped[str] = mapped_column(String(120), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_ref: Mapped[str] = mapped_column(Text, nullable=False)


class SilverHead(Base):
    __tablename__ = "silver_head"
    __table_args__ = ({"schema": "app"},)
    scope: Mapped[str] = mapped_column(String(120), primary_key=True)
    snapshot_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("app.silver_snapshot.snapshot_id")
    )
