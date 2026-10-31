"""Khung bộ điều hợp truy vấn Gold dựa trên DuckDB."""

from __future__ import annotations


class EmptyGoldQueryExecutor:
    """Bộ thay thế có kết quả xác định cho đến khi bộ điều hợp truy vấn Gold được triển khai."""

    async def execute(self, sql: str) -> list[dict[str, object]]:
        """Trả về kết quả rỗng nhưng vẫn tuân thủ hợp đồng cổng đọc."""
        _ = sql
        return []
