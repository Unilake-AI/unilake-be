"""Các mô hình SQLAlchemy cho mô-đun lịch sử truy vấn."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base

__all__ = ["QueryRequest"]


class QueryRequest(Base):
    """Yêu cầu truy vấn ngôn ngữ tự nhiên của người dùng và trạng thái kiểm toán."""

    __tablename__ = "query_request"
    __table_args__ = (
        Index("ix_query_request_app_user_id", "app_user_id"),
        Index("ix_query_request_status", "status"),
        Index("ix_query_request_submitted_at", "submitted_at"),
        {"schema": "app"},
    )

    query_request_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    app_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.app_user.app_user_id"), nullable=False
    )
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str | None] = mapped_column(
        String(20), server_default=text("'vi'"), nullable=True
    )
    detected_intent: Mapped[str | None] = mapped_column(String(150), nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
