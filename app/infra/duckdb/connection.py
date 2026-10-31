"""Cấu hình kết nối DuckDB."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DuckDBConnectionSettings:
    """Tùy chọn kết nối cho truy vấn phân tích tầng Gold."""

    database: str = ":memory:"
    read_only: bool = True
