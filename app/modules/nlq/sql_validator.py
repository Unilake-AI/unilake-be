"""Kiểm tra tính chỉ đọc của SQL do NLQ sinh ra."""

from __future__ import annotations

from app.core.exceptions import AppException


def validate_read_only_sql(sql: str) -> str:
    """Từ chối SQL rõ ràng không an toàn hoặc không chỉ đọc trước khi thực thi."""
    normalized = sql.strip().lower()
    blocked = ("insert ", "update ", "delete ", "drop ", "alter ", "truncate ", "create ")
    if not normalized.startswith("select") or any(keyword in normalized for keyword in blocked):
        raise AppException("Only read-only SELECT queries are allowed", code="INVALID_SQL")
    return sql
