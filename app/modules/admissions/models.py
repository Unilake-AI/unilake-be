"""Audit and correction metadata for admissions workflows."""

from __future__ import annotations

from sqlalchemy import JSON, BigInteger, Column, DateTime, String, Table, Text
from sqlalchemy.dialects.postgresql import JSONB

from app.infra.db.base import Base

CORRECTIONS = Table(
    "admission_correction",
    Base.metadata,
    Column("correction_id", String(36), primary_key=True),
    Column("dataset_id", BigInteger, nullable=False),
    Column("admission_id", BigInteger, nullable=False),
    Column("expected_revision", BigInteger, nullable=False),
    Column("operation", String(40), nullable=False),
    Column("changes", JSON().with_variant(JSONB(), "postgresql"), nullable=False),
    Column("reason", Text, nullable=False),
    Column("status", String(20), nullable=False),
    Column("requested_by", BigInteger, nullable=False),
    Column("applied_by", BigInteger),
    Column("created_at", DateTime(timezone=True)),
    Column("applied_at", DateTime(timezone=True)),
    Column("result", JSON().with_variant(JSONB(), "postgresql")),
    schema="app",
)
EVENTS = Table(
    "admission_event",
    Base.metadata,
    Column("event_id", String(36), primary_key=True),
    Column("admission_id", BigInteger, nullable=False),
    Column("old_revision", BigInteger),
    Column("new_revision", BigInteger, nullable=False),
    Column("operation", String(40), nullable=False),
    Column("actor_id", BigInteger),
    Column("dataset_id", BigInteger),
    Column("gold_job_id", String(36)),
    Column("correction_id", String(36)),
    Column("request_key", String(64)),
    Column("reason", Text),
    Column("source", JSON().with_variant(JSONB(), "postgresql")),
    Column("before_values", JSON().with_variant(JSONB(), "postgresql")),
    Column("after_values", JSON().with_variant(JSONB(), "postgresql")),
    Column("created_at", DateTime(timezone=True)),
    schema="app",
)
