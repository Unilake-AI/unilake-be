"""Các mô hình SQLAlchemy cho mô-đun NLQ."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
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
    "AiModel",
    "GeneratedSql",
    "PromptTemplate",
    "QueryExecution",
    "SchemaRetrieval",
]


class AiModel(Base):
    __tablename__ = "ai_model"
    __table_args__ = (
        Index("ix_ai_model_provider_model_name", "provider", "model_name"),
        Index("ix_ai_model_is_active", "is_active"),
        {"schema": "app"},
    )

    ai_model_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    model_name: Mapped[str] = mapped_column(String(150), nullable=False)
    model_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class PromptTemplate(Base):
    __tablename__ = "prompt_template"
    __table_args__ = (
        UniqueConstraint("template_name", "version", name="uq_prompt_template"),
        Index("ix_prompt_template_is_active", "is_active"),
        {"schema": "app"},
    )

    prompt_template_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    template_name: Mapped[str] = mapped_column(String(150), nullable=False)
    version: Mapped[str] = mapped_column(String(30), nullable=False)
    template_text: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class SchemaRetrieval(Base):
    __tablename__ = "schema_retrieval"
    __table_args__ = (
        UniqueConstraint("query_request_id", "catalog_table_id", name="uq_schema_retrieval"),
        Index("ix_schema_retrieval_query_request_id", "query_request_id"),
        Index("ix_schema_retrieval_catalog_table_id", "catalog_table_id"),
        {"schema": "app"},
    )

    schema_retrieval_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    query_request_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.query_request.query_request_id"), nullable=False
    )
    catalog_table_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.catalog_table.catalog_table_id"), nullable=False
    )
    retrieval_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    relevance_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)


class GeneratedSql(Base):
    __tablename__ = "generated_sql"
    __table_args__ = (
        Index("ix_generated_sql_query_request_id", "query_request_id"),
        Index("ix_generated_sql_ai_model_id", "ai_model_id"),
        Index("ix_generated_sql_prompt_template_id", "prompt_template_id"),
        Index("ix_generated_sql_validation_status", "validation_status"),
        {"schema": "app"},
    )

    generated_sql_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    query_request_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.query_request.query_request_id"), nullable=False
    )
    ai_model_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.ai_model.ai_model_id"), nullable=False
    )
    prompt_template_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.prompt_template.prompt_template_id"), nullable=False
    )
    sql_text: Mapped[str] = mapped_column(Text, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    validation_status: Mapped[str] = mapped_column(String(30), nullable=False)
    validation_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class QueryExecution(Base):
    __tablename__ = "query_execution"
    __table_args__ = (
        Index("ix_query_execution_execution_status", "execution_status"),
        {"schema": "app"},
    )

    query_execution_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    generated_sql_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.generated_sql.generated_sql_id"), unique=True, nullable=False
    )
    executed_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    execution_status: Mapped[str] = mapped_column(String(30), nullable=False)
    row_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    execution_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
