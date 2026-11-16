"""Đọc cùng cấu trúc Silver khi PostgreSQL JSONB đổi thứ tự thuộc tính trong cấu hình."""

from __future__ import annotations

import pytest

from app.modules.pipeline.repositories.silver_storage import SilverError
from app.modules.pipeline.silver.validation import parquet_bytes, read_parquet


def test_parquet_column_order_does_not_change_contract_but_types_still_must_match():
    properties = {
        "id": {"type": "integer", "x-physical-type": "int"},
        "name": {"type": ["string", "null"], "x-physical-type": "utf8"},
    }
    records = [{"id": 7, "name": "AB"}]
    content = parquet_bytes(records, {"properties": properties})
    reversed_spec = {"properties": dict(reversed(list(properties.items())))}
    assert read_parquet(content, reversed_spec, 1) == records
    wrong = {"properties": {**properties, "id": {"type": "integer", "x-physical-type": "bigint"}}}
    with pytest.raises(SilverError, match="PARQUET_SCHEMA_OR_COUNT_MISMATCH"):
        read_parquet(content, wrong, 1)
