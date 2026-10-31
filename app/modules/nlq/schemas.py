"""Các schema Pydantic cho truy vấn bằng ngôn ngữ tự nhiên."""

from __future__ import annotations

from pydantic import BaseModel


class NaturalLanguageQueryRequest(BaseModel):
    """Câu hỏi của người dùng gửi đến bộ xử lý NLQ."""

    question: str


class NaturalLanguageQueryResponse(BaseModel):
    """Dữ liệu phản hồi NLQ."""

    sql: str
    rows: list[dict[str, object]] = []
