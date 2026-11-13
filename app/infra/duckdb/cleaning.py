"""Kết nối DuckDB riêng cho làm sạch, nạp dữ liệu theo lô và giữ nguyên thứ tự dòng."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import duckdb
import pyarrow as pa


class DuckDBCleaningConnection:
    """Mỗi lần chạy dùng bộ nhớ riêng, không dùng chung bảng tạm giữa các yêu cầu."""

    def __init__(self, *, threads: int, memory_limit: str) -> None:
        if type(threads) is not int or threads < 1:
            raise ValueError("INVALID_DUCKDB_THREADS")
        # Để thư viện kiểm tra cấu hình và báo lỗi, không âm thầm bỏ giới hạn bộ nhớ.
        self._connection = duckdb.connect(
            database=":memory:", config={"threads": threads, "memory_limit": memory_limit}
        )

    def execute(
        self, query: str, parameters: Sequence[object] | None = None
    ) -> DuckDBCleaningConnection:
        self._connection.execute(query, parameters)
        return self

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._connection.fetchall())

    def replace_table(
        self,
        name: str,
        columns: Sequence[tuple[str, str]],
        rows: Sequence[tuple[object, ...]],
    ) -> None:
        # Tên ở đây do bộ xử lý đặt, không lấy tên cột trong tệp người dùng làm tên bảng.
        types = {"VARCHAR": pa.string(), "BIGINT": pa.int64(), "BOOLEAN": pa.bool_()}
        table = pa.Table.from_arrays(
            [
                pa.array([row[index] for row in rows], type=types[kind])
                for index, (_, kind) in enumerate(columns)
            ],
            names=[column for column, _ in columns],
        )
        self._connection.register("_cleaning_input", table)
        try:
            quoted = '"' + name.replace('"', '""') + '"'
            self._connection.execute(
                f"CREATE OR REPLACE TABLE {quoted} AS SELECT * FROM _cleaning_input"
            )
        finally:
            self._connection.unregister("_cleaning_input")

    def register_rule(self, name: str, rule: Callable[[int], str]) -> None:
        self._connection.create_function(name, rule, ["BIGINT"], "VARCHAR")

    def close(self) -> None:
        self._connection.close()
