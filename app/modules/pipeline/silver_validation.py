"""Kiểm tra kiểu vật lý nghiêm ngặt, PK/unique/FK trên toàn bộ bản chụp ứng viên."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from app.modules.pipeline.silver_storage import SilverError
from app.modules.pipeline.type_standardization import ConversionError, convert_value


@dataclass(frozen=True)
class SilverIssue:
    entity: str
    field: str
    locator: str
    code: str
    severity: str = "error"


def arrow_type(spec: Mapping[str, Any]) -> pa.DataType:
    physical = spec["x-physical-type"]
    if physical in {"int", "int32"}:
        return pa.int32()
    if physical in {"bigint", "int64"}:
        return pa.int64()
    if physical in {"boolean", "bool"}:
        return pa.bool_()
    if physical in {"utf8", "text"} or physical.startswith("varchar("):
        return pa.string()
    if physical.startswith("decimal("):
        return pa.decimal128(spec["x-precision"], spec["x-scale"])
    if physical == "date32":
        return pa.date32()
    if physical == "timestamp[us,UTC]":
        return pa.timestamp("us", tz="UTC")
    raise SilverError("UNSUPPORTED_PHYSICAL_TYPE")


def arrow_schema(spec: Mapping[str, Any]) -> pa.Schema:
    return pa.schema(
        [
            pa.field(
                n,
                arrow_type(p),
                nullable=isinstance(p["type"], list | tuple) and "null" in p["type"],
            )
            for n, p in spec["properties"].items()
        ]
    )


def native(value: object, spec: Mapping[str, Any]) -> bool:
    if value is None:
        return isinstance(spec["type"], list | tuple) and "null" in spec["type"]
    physical = spec["x-physical-type"]
    if physical in {"int", "int32", "bigint", "int64"}:
        return type(value) is int
    if physical in {"bool", "boolean"}:
        return type(value) is bool
    if physical.startswith("decimal("):
        return type(value) is Decimal
    if physical == "date32":
        return type(value) is date
    if physical == "timestamp[us,UTC]":
        return isinstance(value, datetime) and value.utcoffset() == timedelta(0)
    return type(value) is str


def parquet_bytes(records: Sequence[dict[str, object]], spec: Mapping[str, Any]) -> bytes:
    schema = arrow_schema(spec)
    table = pa.Table.from_pylist(list(records), schema=schema)
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression="snappy", version="2.6")
    data: bytes = sink.getvalue().to_pybytes()
    restored = read_parquet(data, spec, len(records))
    if restored != list(records):
        raise SilverError("PARQUET_VALUE_MISMATCH")
    return data


def read_parquet(data: bytes, spec: Mapping[str, Any], count: int) -> list[dict[str, object]]:
    table = pq.read_table(pa.BufferReader(data))
    if not table.schema.equals(arrow_schema(spec), check_metadata=True) or table.num_rows != count:
        raise SilverError("PARQUET_SCHEMA_OR_COUNT_MISMATCH")
    return table.to_pylist()  # type: ignore[no-any-return]


def validate_snapshot(
    tables: Mapping[str, Sequence[dict[str, object]]], specs: Mapping[str, Any]
) -> tuple[SilverIssue, ...]:
    issues: list[SilverIssue] = []
    valid: dict[str, list[dict[str, object]]] = {}
    for entity, records in tables.items():
        spec = specs[entity]
        props = spec["properties"]
        valid[entity] = []
        for index, row in enumerate(records):
            locator = str(row.get("_source_record_id", f"record:{index}"))
            start = len(issues)
            if set(row) != set(props):
                issues.append(SilverIssue(entity, "*", locator, "TARGET_SHAPE_MISMATCH"))
                continue
            for name, field in props.items():
                value = row[name]
                try:
                    if not native(value, field):
                        raise ConversionError("PHYSICAL_TYPE_MISMATCH")
                    if convert_value(value, field) != value:
                        raise ConversionError("NON_CANONICAL_VALUE")
                    if "enum" in field and value not in field["enum"]:
                        raise ConversionError("UNKNOWN_REFERENCE_CODE")
                    if "const" in field and value != field["const"]:
                        raise ConversionError("CONTRACT_CONST_MISMATCH")
                    if (
                        value is not None
                        and (name in spec["x-primary-key"] or "x-references" in field)
                        and (not isinstance(value, int) or value <= 0)
                    ):
                        raise ConversionError("CANONICAL_ID_INVALID")
                except ConversionError as exc:
                    issues.append(SilverIssue(entity, name, locator, exc.code))
            if len(issues) == start:
                valid[entity].append(row)
        groups = [
            ["_source_record_id"],
            spec["x-primary-key"],
            *spec.get("x-unique-together", []),
            *[[n] for n, p in props.items() if p.get("x-unique-non-null")],
        ]
        for group in groups:
            seen: dict[tuple[object, ...], list[str]] = {}
            for row in valid[entity]:
                key = tuple(row[n] for n in group)
                if all(v is not None for v in key):
                    seen.setdefault(key, []).append(str(row["_source_record_id"]))
            for locators in seen.values():
                if len(locators) > 1:
                    issues.extend(
                        SilverIssue(entity, ",".join(group), loc, "DUPLICATE_KEY")
                        for loc in locators
                    )
    for entity, records in valid.items():
        spec = specs[entity]
        for name, field in spec["properties"].items():
            if "x-references" not in field:
                continue
            ref = field["x-references"]
            targets = {r[ref["column"]] for r in valid.get(ref["entity"], [])}
            for row in records:
                if row[name] is not None and row[name] not in targets:
                    issues.append(
                        SilverIssue(
                            entity, name, str(row["_source_record_id"]), "FK_NOT_IN_SNAPSHOT"
                        )
                    )
            if ref["entity"] == entity:
                edges = {r[ref["column"]]: r[name] for r in records}
                for row in records:
                    current, visited = row[ref["column"]], set()
                    while current is not None and current in edges:
                        if current in visited:
                            issues.append(
                                SilverIssue(
                                    entity, name, str(row["_source_record_id"]), "REFERENCE_CYCLE"
                                )
                            )
                            break
                        visited.add(current)
                        current = edges[current]
    return tuple(issues)
