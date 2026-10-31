"""Các schema Pydantic cho API lịch sử truy vấn."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class QueryHistoryRead(BaseModel):
    """Dữ liệu truy vấn ngôn ngữ tự nhiên đã ghi nhận."""

    id: int
    question: str
    created_at: datetime
