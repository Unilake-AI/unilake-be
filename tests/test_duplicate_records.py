from __future__ import annotations

import csv
import hashlib
import json
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest
from openpyxl import Workbook

from app.modules.ingestion.contracts import FileProvenance
from app.modules.ingestion.readers.contract import ReadRequest
from app.modules.pipeline.duplicate_records import (
    DuplicateKeyRule,
    DuplicateProfile,
    build_duplicate_profile,
    clean_bronze_duplicates,
    detect_and_handle_duplicates,
)

ROOT = Path(__file__).resolve().parents[1]


def provenance() -> FileProvenance:
    return FileProvenance(
        source_system_id="42",
        data_source_id="7",
        dataset_id="11",
        source_type="CSV",
        source_ref="s3://bronze/hr/job-1/employee.csv",
        object_version_id="bronze-v1",
        content_sha256="a" * 64,
        parser_fingerprint="parser",
        columns_fingerprint="columns",
        captured_at="2026-10-19T00:00:00+00:00",
        locator_kind="parsed_record_ordinal",
    )


def profile(
    *,
    collapse: bool = False,
    secondary_unique: bool = False,
) -> DuplicateProfile:
    rules = [DuplicateKeyRule("HR-EMPLOYEE-PK", ("emp_id",), "PRIMARY_KEY")]
    if secondary_unique:
        rules.append(DuplicateKeyRule("HR-EMPLOYEE-CODE", ("code",), "UNIQUE"))
    return DuplicateProfile(
        domain="hr",
        entity="employee",
        version="0.1.0-draft",
        required_source_columns=tuple(
            dict.fromkeys(field for rule in rules for field in rule.fields)
        ),
        key_rules=tuple(rules),
        collapse_exact_primary_key_duplicates=collapse,
    )


def detect(rows, policy=None, columns=None):
    columns = columns or ["emp_id", "code", "name"]
    return detect_and_handle_duplicates(
        columns=columns,
        rows=rows,
        locators=tuple(f"record:{index}" for index in range(1, len(rows) + 1)),
        profile=policy or profile(),
        batch_id="batch-1",
        provenance=provenance(),
    )


def test_schema_builds_primary_unique_and_composite_source_keys():
    schema = {
        "entities": {
            "admission": {
                "primary_key": ["admission_id"],
                "unique_together": [["applicant_id", "year_id", "rank"]],
                "columns": [
                    {"name": "admission_id"},
                    {"name": "applicant_id"},
                    {"name": "year_id"},
                    {"name": "rank"},
                    {"name": "reference_code", "unique": True},
                ],
            }
        }
    }
    result = build_duplicate_profile(
        schema,
        domain="admissions",
        entity="admission",
        version="0.1.0",
        target_to_source={
            "admission_id": "id",
            "applicant_id": "student_ref",
            "year_id": "season",
            "rank": "preference",
            "reference_code": "ref",
        },
    )

    assert [rule.kind for rule in result.key_rules] == [
        "PRIMARY_KEY",
        "UNIQUE",
        "COMPOSITE_UNIQUE",
    ]
    assert [rule.fields for rule in result.key_rules] == [
        ("id",),
        ("ref",),
        ("student_ref", "season", "preference"),
    ]
    assert set(result.required_source_columns) == {
        "id",
        "ref",
        "student_ref",
        "season",
        "preference",
    }


def test_profiles_can_be_built_for_all_owned_entities_in_domain_schemas():
    schema_paths = (
        ("admissions", ROOT / "docs/data-schema/admissions_v1.schema.json"),
        ("academic", ROOT / "docs/data-schema/academic_v1.schema.json"),
        ("hr", ROOT / "docs/data-schema/hr_v1.schema.json"),
    )
    owned_entity_count = 0
    for domain, path in schema_paths:
        schema = json.loads(path.read_text(encoding="utf-8"))
        for entity, details in schema["entities"].items():
            if "primary_key" not in details:
                continue
            owned_entity_count += 1
            target_keys = set(details["primary_key"])
            target_keys.update(
                column["name"]
                for column in details.get("columns", ())
                if column.get("unique") is True
            )
            for key in details.get("unique_together", ()):
                target_keys.update(key)
            build_duplicate_profile(
                schema,
                domain=domain,
                entity=entity,
                version="test",
                target_to_source={field: field for field in target_keys},
            )
    assert owned_entity_count == 22


def test_same_primary_key_and_payload_is_quarantined_without_approval():
    result = detect(
        [
            ("001", "NV001", "Example Person"),
            ("001", "NV001", "Example Person"),
        ]
    )

    assert not result.passed
    assert len(result.quarantined) == 2
    assert {issue.code for issue in result.issues} == {"DUPLICATE_KEY_COLLISION"}


def test_approved_exact_primary_key_duplicate_is_collapsed_with_all_locators():
    result = detect(
        [
            ("001", "NV001", "Example Person"),
            ("001", "NV001", "Example Person"),
        ],
        profile(collapse=True),
    )

    assert len(result.passed) == 1
    assert not result.quarantined
    assert result.passed[0].source_locators == ("record:1", "record:2")
    assert result.issues[0].code == "EXACT_DUPLICATE_COLLAPSED"
    assert result.issues[0].related_locator == "record:1"


def test_conflicting_primary_key_quarantines_every_row_without_selecting_winner():
    result = detect(
        [
            ("001", "NV001", "First version"),
            ("001", "NV001", "Second version"),
            ("002", "NV002", "Unrelated person"),
        ],
        profile(collapse=True),
    )

    assert [record.locator for record in result.quarantined] == ["record:1", "record:2"]
    assert [record.values["name"] for record in result.passed] == ["Unrelated person"]
    assert {issue.code for issue in result.issues} == {"DUPLICATE_PRIMARY_KEY_CONFLICT"}


def test_unique_collision_with_different_primary_keys_quarantines_all():
    result = detect(
        [
            ("001", "SAME-CODE", "First person"),
            ("002", "SAME-CODE", "Second person"),
        ],
        profile(secondary_unique=True),
    )

    assert not result.passed
    assert len(result.quarantined) == 2
    assert {issue.rule_id for issue in result.issues} == {"HR-EMPLOYEE-CODE"}
    assert {issue.code for issue in result.issues} == {"DUPLICATE_KEY_COLLISION"}


def test_nullable_unique_key_and_missing_key_values_do_not_form_duplicate_group():
    policy = DuplicateProfile(
        domain="admissions",
        entity="applicant",
        version="test",
        required_source_columns=("id", "national_id"),
        key_rules=(
            DuplicateKeyRule("ADM-APPLICANT-PK", ("id",), "PRIMARY_KEY"),
            DuplicateKeyRule("ADM-APPLICANT-NATIONAL-ID", ("national_id",), "UNIQUE"),
        ),
    )
    result = detect(
        [("001", None), ("002", None), ("", "123456789012"), ("003", None)],
        policy,
        ["id", "national_id"],
    )

    assert len(result.passed) == 4
    assert not result.quarantined
    assert result.duplicate_groups_by_rule["ADM-APPLICANT-NATIONAL-ID"] == 0


def test_identical_rows_with_different_primary_keys_are_not_exact_row_deduped():
    result = detect(
        [
            ("001", "NV001", "Same descriptive fields"),
            ("002", "NV002", "Same descriptive fields"),
        ]
    )

    assert len(result.passed) == 2
    assert not result.issues


def test_unique_collision_issue_contains_no_raw_values():
    result = detect(
        [
            ("001", "NV001", "Alice Example"),
            ("002", "NV001", "Alice Example"),
        ],
        profile(secondary_unique=True),
    )

    issue_text = repr(result.issues)
    assert "Alice Example" not in issue_text
    assert "NV001" not in issue_text


def bronze_request(extension: str, *, version: str | None = "bronze-v1") -> ReadRequest:
    source_type = "EXCEL" if extension == ".xlsx" else "CSV"
    config = (
        {"sheet_name": "Data", "header_row": 1, "skip_rows": 0}
        if source_type == "EXCEL"
        else {"delimiter": ",", "encoding": "utf-8", "has_header": True}
    )
    return ReadRequest(
        source_type,
        f"s3://bronze/hr/2026/10/19/job-1/employee{extension}",
        config,
        f"employee{extension}",
        version,
    )


def write_fixture(path: Path, extension: str) -> None:
    rows = [
        ("emp_id", "code", "name"),
        ("001", "NV001", "First"),
        ("001", "NV001", "First"),
        ("001", "NV001", "Changed"),
        ("002", "NV002", "Unrelated"),
    ]
    if extension == ".csv":
        with path.open("w", encoding="utf-8", newline="") as stream:
            csv.writer(stream).writerows(rows)
        return
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Data"
    for row in rows:
        sheet.append(row)
    workbook.save(path)
    workbook.close()


@pytest.mark.parametrize("extension", [".csv", ".xlsx"])
def test_clean_bronze_duplicates_reads_versioned_csv_and_xlsx(tmp_path, monkeypatch, extension):
    path = tmp_path / f"employee{extension}"
    write_fixture(path, extension)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()

    @contextmanager
    def resolve_local_fixture(request):
        assert request.object_version_id == "bronze-v1"
        yield replace(request, location=str(path))

    monkeypatch.setattr(
        "app.modules.ingestion.readers.file_snapshot.resolve_file_request",
        resolve_local_fixture,
    )
    result = clean_bronze_duplicates(
        bronze_request(extension),
        profile(collapse=True),
        source_system_id="42",
        data_source_id="7",
        dataset_id="11",
        batch_id="job-1",
        expected_sha256=digest,
        expected_rows=4,
    )

    assert result.records_read == 4
    assert len(result.quarantined) == 3
    assert [record.values["name"] for record in result.passed] == ["Unrelated"]
    first_duplicate_locator = "record:1" if extension == ".csv" else "sheet:Data:row:2"
    assert result.quarantined[0].source_locators == (first_duplicate_locator,)
    assert result.duplicate_groups_by_rule["HR-EMPLOYEE-PK"] == 1
    assert {issue.code for issue in result.issues} == {"DUPLICATE_PRIMARY_KEY_CONFLICT"}
    assert result.provenance.object_version_id == "bronze-v1"
    assert result.provenance.content_sha256 == digest


def test_missing_key_column_is_rejected_as_source_structure_error():
    with pytest.raises(ValueError, match="REQUIRED_SOURCE_FIELD_MISSING:emp_id"):
        detect_and_handle_duplicates(
            columns=["code", "name"],
            rows=[("NV001", "Name")],
            locators=("record:1",),
            profile=profile(),
            batch_id="batch-1",
            provenance=provenance(),
        )


@pytest.mark.parametrize(
    ("extension", "version", "sha256", "rows", "error"),
    [
        (".csv", None, "a" * 64, 1, "BRONZE_OBJECT_VERSION_REQUIRED"),
        (".xlsm", "bronze-v1", "a" * 64, 1, "BRONZE_FILE_TYPE_MISMATCH"),
        (".csv", "bronze-v1", "bad", 1, "BRONZE_OBJECT_CHECKSUM_REQUIRED"),
        (".csv", "bronze-v1", "a" * 64, -1, "BRONZE_OBJECT_ROW_COUNT_REQUIRED"),
    ],
)
def test_bronze_metadata_validation(extension, version, sha256, rows, error):
    with pytest.raises(ValueError, match=error):
        clean_bronze_duplicates(
            bronze_request(extension, version=version),
            profile(),
            source_system_id="42",
            data_source_id="7",
            dataset_id="11",
            batch_id="job-1",
            expected_sha256=sha256,
            expected_rows=rows,
        )


def test_bronze_checksum_mismatch_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / "employee.csv"
    write_fixture(path, ".csv")

    @contextmanager
    def resolve_local_fixture(request):
        yield replace(request, location=str(path))

    monkeypatch.setattr(
        "app.modules.ingestion.readers.file_snapshot.resolve_file_request",
        resolve_local_fixture,
    )
    with pytest.raises(ValueError, match="BRONZE_OBJECT_CHECKSUM_MISMATCH"):
        clean_bronze_duplicates(
            bronze_request(".csv"),
            profile(),
            source_system_id="42",
            data_source_id="7",
            dataset_id="11",
            batch_id="job-1",
            expected_sha256="a" * 64,
            expected_rows=4,
        )
