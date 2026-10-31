"""Kiểm tra sản phẩm S1, chủ động độc lập với bộ thực thi chuẩn hóa."""

from __future__ import annotations

import copy
import csv
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs/standardization"
DOMAINS = ("admissions", "academic", "hr")
CONTRACTS = {
    d: json.loads((DOC / f"silver-schemas/{d}.json").read_text(encoding="utf-8")) for d in DOMAINS
}
SPECS = {e: spec for d in CONTRACTS.values() for e, spec in d["$defs"].items()}
EXPECTED = json.loads(
    (ROOT / "data/standardization/s1/expected-business-records.json").read_text(encoding="utf-8")
)


def snapshot():
    result = copy.deepcopy(EXPECTED)
    for entity, rows in result.items():
        for row in rows:
            row.update(
                _source_system_id="SYNTH_" + SPECS[entity]["x-owner"],
                _source_record_id=json.dumps([row[k] for k in SPECS[entity]["x-primary-key"]]),
                _batch_id="s1-fixture",
                _run_id="s1-run",
                _processed_at="2026-10-14T00:00:00Z",
                _schema_version="0.1.0-draft",
                _mapping_version="synthetic-1",
            )
    return result


def relational_errors(records):
    errors = []
    for entity, spec in SPECS.items():
        rows = records[entity]
        groups = [spec["x-primary-key"], *spec["x-unique-together"]]
        groups += [[n] for n, p in spec["properties"].items() if p.get("x-unique-non-null")]
        for keys in groups:
            values = [
                tuple(r[k] for k in keys) for r in rows if all(r[k] is not None for k in keys)
            ]
            if len(values) != len(set(values)):
                errors.append("DUPLICATE_KEY")
        for row in rows:
            for name, prop in spec["properties"].items():
                value = row[name]
                if value is None:
                    continue
                ref = prop.get("x-references")
                if ref and value not in {r[ref["column"]] for r in records[ref["entity"]]}:
                    errors.append("REFERENCE_PENDING")
                if "x-precision" in prop:
                    dec = Decimal(value)
                    if "x-minimum" in prop and dec < Decimal(str(prop["x-minimum"])):
                        errors.append("VALUE_RANGE_ERROR")
                    if "x-maximum" in prop and dec > Decimal(str(prop["x-maximum"])):
                        errors.append("VALUE_RANGE_ERROR")
    return errors


@pytest.mark.parametrize("domain", DOMAINS)
def test_contract_and_fixture(domain):
    contract = CONTRACTS[domain]
    Draft202012Validator.check_schema(contract)
    source = ROOT / f"docs/data-schema/{domain}_v1.schema.json"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == contract["x-source-sha256"]
    data = snapshot()
    Draft202012Validator(contract, format_checker=FormatChecker()).validate(
        {e: data[e] for e in contract["$defs"]}
    )


def test_all_entities_fields_and_mapping_covered():
    assert len(SPECS) == 22
    assert set(SPECS) == set(EXPECTED)
    with (DOC / "mapping-matrix.csv").open(encoding="utf-8-sig", newline="") as f:
        mappings = list(csv.DictReader(f))
    for domain in DOMAINS:
        source = json.loads(
            (ROOT / f"docs/data-schema/{domain}_v1.schema.json").read_text(encoding="utf-8")
        )
        for entity, spec in source["entities"].items():
            if "columns" not in spec:
                continue
            rows = [r for r in mappings if r["entity"] == entity]
            assert {c["name"] for c in spec["columns"]} <= {r["source_field"] for r in rows}
            targets = [r["target_field"] for r in rows if r["target_field"]]
            assert len(targets) == len(set(targets))
            assert set(targets) == set(SPECS[entity]["properties"])
            assert not set(spec.get("pii_columns", [])) & set(targets)
    catalogue = (DOC / "rules.md").read_text(encoding="utf-8")
    assert all(row["rule_id"] in catalogue for row in mappings)


def test_keys_fk_and_decimal_bounds():
    assert not relational_errors(snapshot())
    assert SPECS["lecturer"]["x-identity-mode"] == "shared_fk"
    assert SPECS["employee_qualification"]["x-primary-key"] == ["employee_id", "qualification_id"]


@pytest.mark.parametrize("entity", list(SPECS))
@pytest.mark.parametrize("mutation", ["missing", "wrong_type", "extra", "duplicate"])
def test_reject_invalid_entity(entity, mutation):
    records = snapshot()
    row = records[entity][0]
    key = SPECS[entity]["x-primary-key"][0]
    if mutation == "duplicate":
        records[entity].append(copy.deepcopy(row))
        assert "DUPLICATE_KEY" in relational_errors(records)
        return
    if mutation == "missing":
        del row[key]
    elif mutation == "wrong_type":
        row[key] = "not-an-integer"
    else:
        row["unmapped_secret"] = "must not publish"
    validator = Draft202012Validator(SPECS[entity], format_checker=FormatChecker())
    assert list(validator.iter_errors(row))


@pytest.mark.parametrize(
    ("entity", "field"),
    [(e, n) for e, s in SPECS.items() for n, p in s["properties"].items() if "x-references" in p],
)
def test_missing_fk(entity, field):
    records = snapshot()
    records[entity][0][field] = 999999
    assert "REFERENCE_PENDING" in relational_errors(records)


@pytest.mark.parametrize(
    ("entity", "field"),
    [(e, n) for e, s in SPECS.items() for n, p in s["properties"].items() if "enum" in p],
)
def test_unknown_enum(entity, field):
    row = snapshot()[entity][0]
    row[field] = "UNKNOWN"
    assert list(Draft202012Validator(SPECS[entity]).iter_errors(row))


def test_date_decimal_and_nullable_unique():
    records = snapshot()
    records["semester"][0]["start_date"] = "2026-02-31"
    assert list(
        Draft202012Validator(SPECS["semester"], format_checker=FormatChecker()).iter_errors(
            records["semester"][0]
        )
    )
    records = snapshot()
    records["enrollment_grade"][0]["grade_value"] = "4.01"
    assert "VALUE_RANGE_ERROR" in relational_errors(records)
    records = snapshot()
    second = copy.deepcopy(records["student"][0])
    second.update(student_id=999, student_code="2026010002", applicant_id=None, admission_id=None)
    records["student"][0].update(applicant_id=None, admission_id=None)
    records["student"].append(second)
    assert not relational_errors(
        records
    )  # unique không khiến trường cho phép null trở thành bắt buộc.
