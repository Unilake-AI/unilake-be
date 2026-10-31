"""Các hợp đồng ứng dụng công khai thuộc mô-đun NLQ."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class LLMPort(Protocol):
    """Khả năng sinh nội dung mà dịch vụ ứng dụng NLQ yêu cầu."""

    async def complete(self, prompt: str) -> str:
        """Trả về nội dung sinh ra cho prompt được cung cấp."""
        ...


class GoldQueryPort(Protocol):
    """Khả năng truy vấn phân tích chỉ đọc mà NLQ yêu cầu."""

    async def execute(self, sql: str) -> list[dict[str, object]]:
        """Thực thi SQL chỉ đọc đã kiểm tra trên dữ liệu Gold đã công bố."""
        ...


@dataclass(frozen=True)
class NLQResult:
    """Kết quả độc lập với giao thức truyền tải do ca sử dụng NLQ trả về."""

    sql: str
    rows: tuple[dict[str, object], ...]
