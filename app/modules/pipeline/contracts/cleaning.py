"""Cách bộ làm sạch yêu cầu tính toán, không phụ thuộc thư viện cơ sở dữ liệu."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Protocol


class CleaningConnection(Protocol):
    """Kết nối riêng cho một lần làm sạch; đóng lại khi lần chạy kết thúc."""

    def execute(
        self, query: str, parameters: Sequence[object] | None = None
    ) -> CleaningConnection: ...

    def fetchall(self) -> list[tuple[Any, ...]]: ...

    def replace_table(
        self,
        name: str,
        columns: Sequence[tuple[str, str]],
        rows: Sequence[tuple[object, ...]],
    ) -> None: ...

    def register_rule(self, name: str, rule: Callable[[int], str]) -> None: ...

    def close(self) -> None: ...
