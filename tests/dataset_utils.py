"""Nạp thiết kế cơ sở dữ liệu nguồn, schema dataset (docs/data-schema/<name>_v1.schema.json) và CSV mẫu."""

from __future__ import annotations

import csv
import json
import re
from datetime import date, datetime
from decimal import Decimal
from functools import cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = REPO_ROOT / "docs" / "data-schema"
SAMPLES_DIR = REPO_ROOT / "data" / "samples"
DATASETS = sorted(
    p.name.removesuffix("_v1.schema.json") for p in SCHEMA_DIR.glob("*_v1.schema.json")
)


SOURCE_DB = SCHEMA_DIR / "unilake-db-architecture.txt"


@cache
def source_db() -> dict[str, list[dict]]:
    """Phân tích thiết kế cơ sở dữ liệu của nhóm (cú pháp Eraser) thành {table: [column, ...]}.

    Mỗi cột là {"name", "db_type", "nullable", "pk", "unique", "references"}; cột
    không có cờ ``nullable`` là NOT NULL, còn ``references`` lấy từ các dòng quan hệ.
    """
    text = SOURCE_DB.read_text(encoding="utf-8")
    refs = {
        src: tuple(dst.split("."))
        for src, dst in re.findall(r"^(\w+\.\w+) [<>-] (\w+\.\w+)$", text, re.M)
    }
    tables: dict[str, list[dict]] = {}
    for match in re.finditer(r"^(\w+) \[[^\]]*\] \{\n(.*?)^\}", text, re.M | re.S):
        table, body = match.groups()
        columns = []
        for line in body.strip().splitlines():
            name, db_type, *flags = line.split()
            columns.append(
                {
                    "name": name,
                    "db_type": db_type,
                    "nullable": "nullable" in flags,
                    "pk": "pk" in flags,
                    "unique": "unique" in flags,
                    "references": refs.get(f"{table}.{name}"),
                }
            )
        tables[table] = columns
    return tables


@cache
def load_schema(dataset: str) -> dict:
    return json.loads((SCHEMA_DIR / f"{dataset}_v1.schema.json").read_text(encoding="utf-8"))


def entity_spec(dataset: str, entity: str) -> dict:
    """Đặc tả thực thể, lần theo {"from_dataset": ...} tới dataset định nghĩa nó."""
    spec = load_schema(dataset)["entities"][entity]
    if "from_dataset" in spec:
        return {**entity_spec(spec["from_dataset"], entity), "owned_by_dataset": False}
    return spec


def entities(dataset: str) -> dict[str, dict]:
    return {name: entity_spec(dataset, name) for name in load_schema(dataset)["entities"]}


@cache
def _read_csv(rel: str) -> tuple[dict[str, str], ...]:
    with (SAMPLES_DIR / rel).open(encoding="utf-8", newline="") as f:
        return tuple(csv.DictReader(f))


def rows(dataset: str, entity: str) -> tuple[dict[str, str], ...]:
    return _read_csv(entity_spec(dataset, entity)["file"])


def index(dataset: str, entity: str) -> dict[str, dict[str, str]]:
    (pk,) = entity_spec(dataset, entity)["primary_key"]
    return {r[pk]: r for r in rows(dataset, entity)}


def parse(value: str, sql_type: str) -> object:
    """Phân tích ô CSV theo kiểu DBML; báo ValueError khi giá trị không phù hợp."""
    base, _, args = sql_type.partition("(")
    args = args.rstrip(")")
    if base in {"int", "bigint"}:
        return int(value)
    if base == "decimal":
        precision, scale = (int(x) for x in args.split(","))
        number = Decimal(value)
        exponent = number.as_tuple().exponent
        if not isinstance(exponent, int) or -exponent > scale:
            raise ValueError(f"{value} does not fit {sql_type}")
        if len(number.as_tuple().digits) > precision:
            raise ValueError(f"{value} does not fit {sql_type}")
        return number
    if base == "timestamp":
        return datetime.fromisoformat(value)
    if base == "date":
        return date.fromisoformat(value)
    if base == "boolean":
        if value not in {"true", "false"}:
            raise ValueError(value)
        return value == "true"
    if base == "varchar":
        if len(value) > int(args):
            raise ValueError(f"{value!r} longer than {sql_type}")
        return value
    if base == "text":
        return value
    raise AssertionError(f"unsupported type {sql_type}")
