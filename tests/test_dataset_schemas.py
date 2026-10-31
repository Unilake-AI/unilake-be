"""Kiểm tra chung cho mọi schema dataset: khớp cơ sở dữ liệu nguồn, CSV mẫu, khóa, FK và DDL."""

from __future__ import annotations

import csv
import re
from collections import Counter
from decimal import InvalidOperation

import pytest

from tests.dataset_utils import (
    DATASETS,
    SAMPLES_DIR,
    SCHEMA_DIR,
    SOURCE_DB,
    entities,
    load_schema,
    parse,
    rows,
    source_db,
)

ENTITY_CASES = [(d, e) for d in DATASETS for e in load_schema(d)["entities"]]
ENTITY_IDS = [f"{d}.{e}" for d, e in ENTITY_CASES]


def test_datasets_found() -> None:
    assert {"admissions", "academic", "hr"} <= set(DATASETS)


SQL_TYPE_FOR_DB_TYPE = {
    "bigint": r"bigint",
    "int": r"int",
    "string": r"varchar\(\d+\)",
    "decimal": r"decimal\(\d+,\d+\)",
    "text": r"text",
    "date": r"date",
    "timestamp": r"timestamp",
    "boolean": r"boolean",
}


@pytest.mark.parametrize("dataset", DATASETS)
def test_schema_matches_source_db(dataset: str) -> None:
    db = source_db()
    for name, spec in entities(dataset).items():
        assert name in db, f"table {name} missing in {SOURCE_DB.name}"
        db_cols = db[name]
        assert [c["name"] for c in spec["columns"]] == [c["name"] for c in db_cols], name
        for col, db_col in zip(spec["columns"], db_cols, strict=True):
            where = f"{name}.{col['name']}"
            assert col["db_type"] == db_col["db_type"], f"{where}: db_type"
            assert re.fullmatch(SQL_TYPE_FOR_DB_TYPE[db_col["db_type"]], col["type"]), where
            assert col["nullable"] == db_col["nullable"], f"{where}: nullable"
            assert bool(col.get("unique")) == db_col["unique"], f"{where}: unique"
            ref = col.get("references")
            assert (ref and (ref["entity"], ref["column"])) == (
                db_col["references"] or None
            ), f"{where}: references"
        db_pk = [c["name"] for c in db_cols if c["pk"]]
        assert spec["primary_key"] == db_pk, f"{name}: primary key"


@pytest.mark.parametrize("dataset", DATASETS)
def test_load_order_covers_all_entities_and_respects_fks(dataset: str) -> None:
    order = load_schema(dataset)["load_order"]
    specs = entities(dataset)
    assert sorted(order) == sorted(specs)
    for name, spec in specs.items():
        for col in spec["columns"]:
            ref = col.get("references")
            if ref and ref["entity"] != name:
                assert ref["entity"] in specs, f"{dataset}: {name}.{col['name']} refs unknown"
                assert order.index(ref["entity"]) < order.index(name), f"{name}.{col['name']}"


@pytest.mark.parametrize(("dataset", "entity"), ENTITY_CASES, ids=ENTITY_IDS)
def test_header_matches_schema(dataset: str, entity: str) -> None:
    spec = entities(dataset)[entity]
    with (SAMPLES_DIR / spec["file"]).open(encoding="utf-8", newline="") as f:
        header = next(csv.reader(f))
    assert header == [c["name"] for c in spec["columns"]]


@pytest.mark.parametrize(("dataset", "entity"), ENTITY_CASES, ids=ENTITY_IDS)
def test_columns_types_nullability_and_constraints(dataset: str, entity: str) -> None:
    data = rows(dataset, entity)
    assert data, f"{entity} sample is empty"
    for col in entities(dataset)[entity]["columns"]:
        name = col["name"]
        for i, row in enumerate(data, start=2):
            raw = row[name]
            where = f"{entity} line {i} column {name}"
            if raw == "":
                assert col["nullable"], f"{where}: required"
                continue
            try:
                value = parse(raw, col["type"])
            except (ValueError, InvalidOperation) as exc:
                pytest.fail(f"{where}: {exc}")
            if "enum" in col:
                assert raw in col["enum"], f"{where}: {raw!r} not in enum"
            if "pattern" in col:
                assert re.fullmatch(col["pattern"], raw), f"{where}: {raw!r} !~ {col['pattern']}"
            if "min" in col:
                assert value >= col["min"], f"{where}: below min"
            if "max" in col:
                assert value <= col["max"], f"{where}: above max"


@pytest.mark.parametrize(("dataset", "entity"), ENTITY_CASES, ids=ENTITY_IDS)
def test_primary_and_unique_keys(dataset: str, entity: str) -> None:
    spec = entities(dataset)[entity]
    keys = [spec["primary_key"], *spec.get("unique_together", [])]
    keys += [[c["name"]] for c in spec["columns"] if c.get("unique")]
    for key in keys:
        values = [tuple(r[k] for k in key) for r in rows(dataset, entity) if all(r[k] for k in key)]
        dupes = [v for v, n in Counter(values).items() if n > 1]
        assert not dupes, f"{entity} duplicate {key}: {dupes[:5]}"


@pytest.mark.parametrize(("dataset", "entity"), ENTITY_CASES, ids=ENTITY_IDS)
def test_foreign_keys_resolve(dataset: str, entity: str) -> None:
    for col in entities(dataset)[entity]["columns"]:
        if not (ref := col.get("references")):
            continue
        targets = {r[ref["column"]] for r in rows(dataset, ref["entity"])}
        missing = {r[col["name"]] for r in rows(dataset, entity) if r[col["name"]]} - targets
        assert not missing, f"{entity}.{col['name']} -> {ref['entity']}: {sorted(missing)[:5]}"


@pytest.mark.parametrize("dataset", DATASETS)
def test_ddl_matches_schema(dataset: str) -> None:
    ddl = (SCHEMA_DIR / f"{dataset}_v1.sql").read_text(encoding="utf-8")
    for name, spec in entities(dataset).items():
        if not spec["owned_by_dataset"]:
            continue
        match = re.search(rf"CREATE TABLE IF NOT EXISTS {name} \((.*?)\n\);", ddl, re.S)
        assert match, f"missing DDL for {name}"
        body = match.group(1)
        lines = dict(re.findall(r"^\s{4}([a-z_]+)\s+(.*?),?$", body, re.M))
        assert list(lines) == [c["name"] for c in spec["columns"]], name
        single_pk = spec["primary_key"] if len(spec["primary_key"]) == 1 else []
        for col in spec["columns"]:
            line = lines[col["name"]].upper()
            where = f"{name}.{col['name']}"
            sql_type = col["type"].upper()
            if col["name"] in single_pk and sql_type == "BIGINT" and "REFERENCES" not in line:
                sql_type = "BIGSERIAL"
            assert line.startswith(sql_type), f"{where}: type"
            is_pk = "PRIMARY KEY" in line or col["name"] in spec["primary_key"]
            assert ("NOT NULL" in line or is_pk) == (not col["nullable"]), f"{where}: NOT NULL"
            assert ("UNIQUE" in line) == bool(col.get("unique")), f"{where}: UNIQUE"
            ref = col.get("references")
            if ref:
                assert f"REFERENCES {ref['entity']} ({ref['column']})".upper() in line, where
            else:
                assert "REFERENCES" not in line, f"{where}: unexpected FK"
        if len(spec["primary_key"]) > 1:
            assert f"PRIMARY KEY ({', '.join(spec['primary_key'])})" in body, f"{name}: PK"
        for key in spec.get("unique_together", []):
            assert f"UNIQUE ({', '.join(key)})" in body, f"{name}: UNIQUE {key}"
