from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.modules.pipeline.identity_contracts import (
    CrosswalkEntry,
    CrosswalkSnapshot,
    IdentityConfigurationError,
    source_key,
)
from app.modules.pipeline.identity_standardization import (
    IdentityStandardizationService,
    ValueDictionary,
)
from app.modules.pipeline.schema_mapping import MappedRow, MappingKey, MappingResult
from app.modules.pipeline.type_standardization import (
    ConversionConfigurationError,
    TypeStandardizationService,
)

ROOT = Path(__file__).resolve().parents[1]
AT = datetime(2026, 1, 1, tzinfo=UTC)
RECORDS = json.loads(
    (ROOT / "data/standardization/s1/expected-business-records.json").read_text(encoding="utf-8")
)
CONTRACTS = {
    domain: json.loads(
        (ROOT / f"docs/standardization/silver-schemas/{domain}.json").read_text(encoding="utf-8")
    )
    for domain in ("admissions", "academic", "hr")
}


def snapshot():
    entries = []
    for contract in CONTRACTS.values():
        for entity, spec in contract["$defs"].items():
            pk = spec["x-primary-key"]
            if len(pk) == 1:
                raw = RECORDS[entity][0][pk[0]]
                entries.append(
                    CrosswalkEntry("SYNTH", entity, (str(raw),), raw + 10000, "fixture-only", AT)
                )
    return CrosswalkSnapshot("fixture-cw-1", tuple(entries))


def setup(
    domain="hr", entity="position", *, dictionaries=None, cw=None, refs=None, value_fields=()
):
    contract = CONTRACTS[domain]
    key = MappingKey("SYNTH", domain, entity, "1", "map", "1", contract["x-version"])
    types = TypeStandardizationService(
        key=key, conversion_version="types-1", contract=contract, value_mapping_fields=value_fields
    )
    props = types.spec["properties"]
    dictionaries = (
        dictionaries
        if dictionaries is not None
        else {n: ValueDictionary("dict-1") for n, s in props.items() if "enum" in s}
    )
    refs = (
        refs if refs is not None else {n: "SYNTH" for n, s in props.items() if "x-references" in s}
    )
    engine = IdentityStandardizationService(
        types=types,
        version="s4-1",
        crosswalk=cw or snapshot(),
        dictionaries=dictionaries,
        reference_namespaces=refs,
        effective_at=AT,
    )
    return types, engine


def typed(types, changes=None, count=1):
    raw = {**RECORDS[types.key.entity][0], **(changes or {})}
    year = raw.pop("birth_year", None)
    locators = [f"record:{i}" for i in range(count)]
    mapped = MappingResult(
        types.key,
        "b",
        "v1",
        tuple(MappedRow(loc, raw) for loc in locators),
        (),
        (),
        count,
        count,
        False,
    )
    return types.standardize(
        mapped,
        derivation_inputs={loc: {"date_of_birth": f"{year}-01-01"} for loc in locators}
        if year
        else None,
        derivation_input_version="v1" if year else None,
    )


@pytest.mark.parametrize(
    ("domain", "entity"), [(d, e) for d, c in CONTRACTS.items() for e in c["$defs"]]
)
def test_all_22_entities_identity_and_canonical_values(domain, entity):
    types, engine = setup(domain, entity)
    intermediate = typed(types)
    output = engine.standardize(intermediate)
    assert not output.issues
    assert len(output.accepted) == 1
    row = output.accepted[0].values
    props = types.spec["properties"]
    for name, value in RECORDS[entity][0].items():
        if value is not None and (
            name in types.spec["x-primary-key"] or "x-references" in props[name]
        ):
            assert row[name] == value + 10000
        elif "enum" in props[name]:
            assert row[name] == value
    assert "pending_s4" not in vars(output.accepted[0])
    assert output.input_version == "v1"
    assert output.crosswalk_fingerprint == snapshot().fingerprint
    assert output.type_issues == intermediate.issues
    assert not output.silver_ready


def test_alias_longer_than_target_is_checked_after_mapping():
    alias = "Chức danh nhân viên theo hệ thống nguồn " * 8
    types, engine = setup(
        dictionaries={"position_level": ValueDictionary("vn-1", ((alias, "Staff"),))}
    )
    result = engine.standardize(typed(types, {"position_level": alias}))
    assert result.accepted[0].values["position_level"] == "Staff"


def test_declared_business_code_mapping_and_case():
    types, engine = setup(
        value_fields=("position_code",),
        dictionaries={
            "position_level": ValueDictionary("enum-1", trim=True, case="casefold"),
            "position_code": ValueDictionary(
                "codes-1", (("giảng viên", "GV"),), trim=True, allow_canonical=False
            ),
        },
    )
    result = engine.standardize(
        typed(types, {"position_code": " giảng viên ", "position_level": " STAFF "})
    )
    assert result.accepted[0].values["position_code"] == "GV"
    assert result.accepted[0].values["position_level"] == "Staff"
    unknown = engine.standardize(typed(types, {"position_code": "unknown"}))
    assert not unknown.accepted
    assert unknown.issues[0].code == "UNKNOWN_REFERENCE_CODE"


@pytest.mark.parametrize(
    ("field", "raw", "code"),
    [
        ("position_id", "000102", "SOURCE_ID_UNMAPPED"),
        ("position_level", "SECRET_UNMAPPED", "UNKNOWN_REFERENCE_CODE"),
    ],
)
def test_unknown_values_reject_without_raw_values(field, raw, code):
    types, engine = setup()
    result = engine.standardize(typed(types, {field: raw}))
    assert result.rejected_locators == ("record:0",)
    assert result.issues[0].code == code
    assert raw not in repr(result.issues)


def test_fk_namespace_is_not_inferred():
    with pytest.raises(IdentityConfigurationError, match="REFERENCE_NAMESPACE_REQUIRED"):
        setup("hr", "assignment", refs={})
    types, engine = setup(
        "hr",
        "assignment",
        refs={"employee_id": "OTHER", "department_id": "SYNTH", "position_id": "SYNTH"},
    )
    result = engine.standardize(typed(types))
    assert [(i.field, i.code) for i in result.issues] == [("employee_id", "REFERENCE_PENDING")]


def test_crosswalk_effective_interval_and_ambiguity():
    entry = CrosswalkEntry(
        "SYNTH", "position", ("102",), 50, "fixture", AT, datetime(2026, 2, 1, tzinfo=UTC)
    )
    cw = CrosswalkSnapshot("1", (entry,))
    types, engine = setup(cw=cw)
    assert engine.standardize(typed(types)).accepted[0].values["position_id"] == 50
    assert entry.valid_to is not None
    expired = IdentityStandardizationService(
        types=types,
        version="s4",
        crosswalk=cw,
        dictionaries=engine.dictionaries,
        reference_namespaces={},
        effective_at=entry.valid_to,
    )
    assert expired.standardize(typed(types)).issues[0].code == "SOURCE_ID_UNMAPPED"
    with pytest.raises(IdentityConfigurationError, match="AMBIGUOUS_IDENTITY"):
        CrosswalkSnapshot("bad", (entry, entry))


def test_canonical_collision_rejects_all_rows_and_preserves_upstream_counts():
    types, engine = setup()
    result = engine.standardize(typed(types, count=2))
    assert not result.accepted
    assert set(result.rejected_locators) == {"record:0", "record:1"}
    assert {i.code for i in result.issues} == {"CANONICAL_KEY_COLLISION"}
    assert result.inspected_count == result.total_count == 2


def test_s3_errors_are_not_lost():
    types, engine = setup()
    result = engine.standardize(typed(types, {"position_name": None}))
    assert not result.accepted
    assert result.rejected_locators == ("record:0",)
    assert result.type_issues[0].code == "REQUIRED_VALUE_MISSING"


@pytest.mark.parametrize(
    "rule",
    [
        ValueDictionary("1", (("x", "Invalid"),)),
        ValueDictionary("1", (("x", "Staff"), ("X", "Staff")), case="lower"),
        ValueDictionary("1", (("staff", "Manager"),), case="lower"),
    ],
)
def test_bad_dictionary_rejected_before_rows(rule):
    with pytest.raises(IdentityConfigurationError):
        setup(dictionaries={"position_level": rule})


def test_versions_fingerprints_and_config_immutable():
    dictionaries = {"position_level": ValueDictionary("1")}
    types, engine = setup(dictionaries=dictionaries)
    dictionaries.clear()
    assert engine.standardize(typed(types)).accepted
    with pytest.raises(FrozenInstanceError):
        engine.version = "changed"  # pyright: ignore[reportAttributeAccessIssue] -- kiểm tra cấu hình bị đóng băng
    with pytest.raises(TypeError):
        engine.references["x"] = "bad"  # pyright: ignore[reportIndexIssue] -- kiểm tra ánh xạ bất biến
    with pytest.raises(IdentityConfigurationError, match="TYPE_HANDOFF_MISMATCH"):
        engine.standardize(replace(typed(types), configuration_fingerprint="changed"))


def test_value_mapping_cannot_defer_numeric_or_identity_fields():
    for field in ("position_id", "missing", "_processed_at"):
        with pytest.raises(ConversionConfigurationError, match="INVALID_VALUE_MAPPING_FIELDS"):
            setup(value_fields=(field,))
    with pytest.raises(ConversionConfigurationError, match="INVALID_VALUE_MAPPING_FIELDS"):
        setup("admissions", "admission_method", value_fields=("max_score",))


def test_source_key_does_not_strip_or_lose_zeroes():
    assert source_key((7,)) == source_key(("7",))
    assert source_key(("007",)) != source_key((7,))
    assert source_key((" 7 ",)) != source_key((7,))
    assert source_key(("1", "23")) != source_key(("12", "3"))


def test_same_identity_across_sources_requires_explicit_crosswalk():
    types, engine = setup()
    entry = CrosswalkEntry("SECOND", "position", ("007",), 10102, "explicit-fixture-match", AT)
    crosswalk = CrosswalkSnapshot("fixture-2", (*snapshot().entries, entry))
    other_types = TypeStandardizationService(
        key=replace(types.key, source_system_id="SECOND"),
        conversion_version="types-1",
        contract=CONTRACTS["hr"],
    )
    other = IdentityStandardizationService(
        types=other_types,
        version="s4-1",
        crosswalk=crosswalk,
        dictionaries=engine.dictionaries,
        reference_namespaces={},
        effective_at=AT,
    )
    assert (
        other.standardize(typed(other_types, {"position_id": "007"}))
        .accepted[0]
        .values["position_id"]
        == engine.standardize(typed(types)).accepted[0].values["position_id"]
    )


def test_composite_key_keeps_both_components():
    types, engine = setup("hr", "employee_qualification")
    row = engine.standardize(typed(types)).accepted[0].values
    assert (row["employee_id"], row["qualification_id"]) == (10104, 10103)
    result = engine.standardize(typed(types, count=2))
    assert not result.accepted
    assert len(result.rejected_locators) == 2


def test_shared_lecturer_mapping_cannot_disagree_with_employee():
    with pytest.raises(IdentityConfigurationError, match="SHARED_IDENTITY_MISMATCH"):
        CrosswalkSnapshot(
            "bad", (CrosswalkEntry("SYNTH", "lecturer", ("104",), 99, "fixture", AT),)
        )
    cw = snapshot()
    employee = CrosswalkEntry("OTHER", "employee", ("104",), 999, "fixture", AT)
    types, engine = setup(
        "hr",
        "lecturer",
        cw=CrosswalkSnapshot("2", (*cw.entries, employee)),
        refs={"employee_id": "OTHER"},
    )
    assert engine.standardize(typed(types)).issues[0].code == "SHARED_IDENTITY_MISMATCH"


@pytest.mark.parametrize(
    ("case", "field", "expected"),
    [
        ("admissions-flat", "admission_method_id", 100007),
        ("admissions-nested", "admission_method_id", 100007),
        ("academic-flat", "status", "Ongoing"),
        ("academic-nested", "status", "Ongoing"),
        ("hr-flat", "position_level", "Staff"),
        ("hr-nested", "position_level", "Staff"),
    ],
)
def test_s4_cli_preview(case, field, expected):
    process = subprocess.run(
        [sys.executable, str(ROOT / "scripts/standardization/preview_s4.py"), "--case", case],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    result = json.loads(process.stdout)
    assert result["accepted"][0]["values"][field] == expected
    assert result["preview"] is True
    assert result["silver_ready"] is False
    assert not result["issues"]
