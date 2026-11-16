"""Tạo cấu trúc Silver giữ mã nguồn từ mẫu đã chọn hoặc các cột thực tế của file."""

from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from typing import Any


def column_names(names: list[str]) -> dict[str, str]:
    """Đặt tên cột hợp lệ, phân biệt tên trùng và giữ liên hệ với tên gốc trong file."""
    result = {}
    for index, source in enumerate(names, 1):
        target = re.sub(r"[^a-z0-9_]+", "_", source.casefold()).strip("_")
        if not target or not target[0].isalpha() or target.startswith("_"):
            target = f"column_{index}"
        initial = target
        suffix = 1
        while target in result:
            suffix += 1
            target = f"{initial}_{suffix}"
        result[target] = source
    return result


def source_contract(
    domain: str,
    entity: str,
    schema: dict[str, Any],
    template: dict[str, Any],
    identifiers: set[str],
    primary_key: list[str],
) -> dict[str, Any]:
    """Chỉ đổi quy định định danh; các quy định bảo vệ dữ liệu và kiểu khác vẫn được giữ."""
    spec = deepcopy(template)
    spec["x-identity-mode"] = "source"
    spec["x-owner"] = domain
    spec["x-primary-key"] = primary_key or ["_source_record_id"]
    properties = spec["properties"]
    for name in identifiers:
        nullable = schema["entities"][entity]["columns"]
        nullable = next(column["nullable"] for column in nullable if column["name"] == name)
        properties[name] = {
            "type": ["string", "null"] if nullable else "string",
            "x-physical-type": "utf8",
            "x-source-identifier": True,
        }
        for column in schema["entities"][entity]["columns"]:
            if column["name"] == name:
                column.update(type="text", db_type="string")
                column["source_identifier"] = True
                for constraint in ("min", "max", "pattern", "enum"):
                    column.pop(constraint, None)
    for prop in properties.values():
        # Mã liên kết cũng là mã của nguồn; chưa có yêu cầu hợp nhất thì chưa kiểm tra nguồn khác.
        prop.pop("x-references", None)
    properties["_schema_version"]["const"] = "source-1"
    spec["required"] = list(properties)
    return {"x-version": "source-1", "$defs": {entity: spec}}


def generic_schema(
    domain: str,
    entity: str,
    mapping: dict[str, str],
    field_types: Mapping[str, str],
    required: set[str],
    identifiers: set[str],
    primary_key: list[str],
    audit: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Cột chưa được xác nhận đổi kiểu được giữ dạng chữ; không tạo khóa nghiệp vụ thay người dùng."""
    kinds = {
        "string": ("string", "utf8", "string"),
        "integer": ("integer", "int64", "bigint"),
        "decimal": ("string", "decimal(38,10)", "decimal"),
        "date": ("string", "date32", "date"),
        "timestamp": ("string", "timestamp[us,UTC]", "timestamp"),
        "boolean": ("boolean", "boolean", "boolean"),
    }
    props = {}
    columns = []
    for name in mapping:
        kind = field_types.get(name, "string")
        logical, physical, source_kind = kinds[kind]
        nullable = name not in required and name not in primary_key
        prop: dict[str, Any] = {
            "type": [logical, "null"] if nullable else logical,
            "x-physical-type": physical,
        }
        column: dict[str, Any] = {
            "name": name,
            "db_type": source_kind,
            "type": "text" if kind == "string" else source_kind,
            "nullable": nullable,
        }
        if kind == "decimal":
            prop.update({"x-precision": 38, "x-scale": 10})
            column["type"] = "decimal(38,10)"
        props[name] = prop
        columns.append(column)
    props.update(deepcopy(audit))
    source = {
        "version": "source-1",
        "file_format": {"date_format": "YYYY-MM-DD"},
        "entities": {entity: {"primary_key": primary_key, "columns": columns}},
    }
    template = {"type": "object", "additionalProperties": False, "properties": props}
    contract = source_contract(domain, entity, source, template, identifiers, primary_key)
    return source, contract
