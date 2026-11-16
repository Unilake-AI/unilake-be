"""Tổng hợp dữ liệu Silver bằng quy định có phiên bản, giữ riêng từng nguồn dữ liệu."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import duckdb
import pyarrow as pa

RECIPE_VERSION = "group-counts-v1"
# Chỉ đếm theo các cột đã biết; không đoán loại dữ liệu từ tên file hay tự tính KPI.
DIMENSIONS: dict[str, dict[str, tuple[str, ...]]] = {
    "hr": {
        "employee": ("gender", "employee_status", "birth_year"),
        "assignment": ("department_id", "position_id", "assignment_status"),
        "lecturer": ("academic_degree", "academic_title"),
    },
    "academic": {
        "student": ("gender", "student_status", "program_id", "admission_year_id"),
        "class": ("course_id", "semester_id", "class_status"),
        "enrollment": ("class_id", "enrollment_status"),
        "semester": ("academic_year", "status"),
        "course": ("status",),
    },
    "admissions": {
        "applicant": ("gender", "province"),
        "admission": ("program_id", "admission_year_id", "admission_method_id", "admission_status"),
        "program": ("major_id", "degree_level", "status"),
        "major": ("department_id", "status"),
    },
}
RECIPE_FINGERPRINT = hashlib.sha256(
    json.dumps(
        {
            "version": RECIPE_VERSION,
            "dimensions": DIMENSIONS,
            "aggregation": "COUNT_ALL_ROWS_GROUP_EACH_FIELD_INCLUDE_NULL_PRESERVE_IDENTIFIER_TYPE",
        },
        sort_keys=True,
    ).encode()
).hexdigest()


def aggregate(domain: str, entity: str, table: Any) -> list[dict[str, Any]]:
    """Số dòng là số bản ghi Silver, không mặc định là số người duy nhất toàn trường."""
    if domain not in DIMENSIONS:
        raise ValueError("GOLD_DOMAIN_UNSUPPORTED")
    result = [
        {"entity": entity, "metric": "record_count", "dimensions": {}, "value": table.num_rows}
    ]
    with duckdb.connect(":memory:") as connection:
        connection.execute("SET threads=2")
        connection.execute("SET memory_limit='512MB'")
        if table.num_columns == 0:
            return result
        connection.register("silver_input", table)
        for field in DIMENSIONS[domain].get(entity, ()):
            if field not in table.column_names:
                continue
            # Tên cột chỉ lấy từ danh sách trong mã nguồn. Giá trị mã vẫn giữ kiểu gốc.
            dtype = table.schema.field(field).type
            if not (
                pa.types.is_string(dtype)
                or pa.types.is_integer(dtype)
                or pa.types.is_boolean(dtype)
            ):
                raise ValueError("GOLD_DIMENSION_TYPE_INVALID")
            groups = connection.execute(
                f'SELECT "{field}", count(*) FROM silver_input GROUP BY "{field}" ORDER BY "{field}" NULLS FIRST'
            ).fetchall()
            if len(groups) > 100_000:
                raise ValueError("GOLD_TOO_MANY_GROUPS")
            for value, count in groups:
                result.append(
                    {
                        "entity": entity,
                        "metric": "record_count_by_" + field,
                        "dimensions": {field: value},
                        "value": count,
                    }
                )
    return result


def parquet_rows(rows: list[dict[str, Any]]) -> Any:
    """Lưu nhóm thống kê thành chuỗi JSON để các bảng Gold có cùng cấu trúc."""
    return pa.Table.from_pylist(
        [
            {**row, "dimensions": json.dumps(row["dimensions"], ensure_ascii=False, sort_keys=True)}
            for row in rows
        ],
        schema=pa.schema(
            [
                ("entity", pa.string()),
                ("metric", pa.string()),
                ("dimensions", pa.string()),
                ("value", pa.int64()),
            ]
        ),
    )
