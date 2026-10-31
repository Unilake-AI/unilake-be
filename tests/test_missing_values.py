from __future__ import annotations

import csv
import hashlib
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest
from openpyxl import Workbook

from app.modules.ingestion.contracts import FileProvenance
from app.modules.ingestion.readers.contract import ReadRequest
from app.modules.pipeline.missing_values import (
    MissingValueProfile,
    MissingValueRule,
    apply_missing_value_rules,
    clean_bronze_object,
)


def profile() -> MissingValueProfile:
    return MissingValueProfile(
        domain="hr",
        entity="employee",
        version="0.1.0-draft",
        required_source_columns=("employee_code", "email", "active"),
        rules=(
            MissingValueRule("employee_code", nullable=False),
            MissingValueRule("email", nullable=True, null_tokens=("", "N/A")),
            MissingValueRule("active", nullable=False),
        ),
    )


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
        ("employee_code", "email", "active"),
        ("E001", "N/A", "false"),
        ("", "person@example.com", "true"),
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
def test_clean_bronze_object_parses_pinned_file_and_handles_missing_values(
    tmp_path, monkeypatch, extension
):
    path = tmp_path / f"employee{extension}"
    write_fixture(path, extension)
    expected_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    request = bronze_request(extension)

    @contextmanager
    def resolve_local_fixture(resolved_request):
        assert resolved_request.object_version_id == "bronze-v1"
        yield replace(resolved_request, location=str(path))

    monkeypatch.setattr(
        "app.modules.ingestion.readers.file_snapshot.resolve_file_request",
        resolve_local_fixture,
    )

    result = clean_bronze_object(
        request,
        profile(),
        source_system_id="42",
        data_source_id="7",
        dataset_id="11",
        batch_id="job-1",
        expected_sha256=expected_sha256,
        expected_rows=2,
    )

    assert result.records_read == 2
    assert len(result.passed) == 1
    assert len(result.quarantined) == 1
    assert result.passed[0].locator == ("record:1" if extension == ".csv" else "sheet:Data:row:2")
    assert result.passed[0].values == {
        "employee_code": "E001",
        "email": None,
        "active": "false",
    }
    assert result.quarantined[0].values["employee_code"] is None
    assert result.missing_values_by_field == {"email": 1, "employee_code": 1}
    assert [(issue.field, issue.code, issue.disposition) for issue in result.issues] == [
        ("email", "NULL_VALUE_PRESERVED", "PRESERVED"),
        ("employee_code", "REQUIRED_VALUE_MISSING", "QUARANTINED"),
    ]
    assert result.provenance.object_version_id == "bronze-v1"
    assert result.provenance.content_sha256 == expected_sha256
    assert result.provenance.source_ref == request.location
    assert path.exists()


def test_missing_column_is_an_input_structure_error():
    with pytest.raises(ValueError, match="REQUIRED_SOURCE_FIELD_MISSING:active"):
        apply_missing_value_rules(
            columns=["employee_code", "email"],
            rows=[("E001", None)],
            locators=("record:1",),
            profile=profile(),
            batch_id="job-1",
            provenance=provenance(),
        )


def test_unconfigured_token_and_false_are_not_treated_as_missing():
    result = apply_missing_value_rules(
        columns=["employee_code", "email", "active"],
        rows=[("E001", " N/A ", False)],
        locators=("record:1",),
        profile=profile(),
        batch_id="job-1",
        provenance=provenance(),
    )

    assert result.passed[0].values["email"] == " N/A "
    assert result.passed[0].values["active"] is False
    assert not result.issues


def test_trim_for_token_match_is_explicit():
    policy = MissingValueProfile(
        "hr",
        "employee",
        "0.1.0-draft",
        ("email",),
        (
            MissingValueRule(
                "email",
                nullable=True,
                null_tokens=("", "N/A"),
                trim_for_token_match=True,
            ),
        ),
    )
    result = apply_missing_value_rules(
        columns=["email"],
        rows=[(" N/A ",)],
        locators=("record:1",),
        profile=policy,
        batch_id="job-1",
        provenance=provenance(),
    )

    assert result.passed[0].values["email"] is None
    assert result.missing_values_by_field == {"email": 1}


def test_unconfigured_csv_null_token_is_preserved_by_parser_and_cleaner(tmp_path, monkeypatch):
    path = tmp_path / "employee.csv"
    path.write_text(
        "employee_code,email,active\nE001,NULL,false\n",
        encoding="utf-8",
    )
    request = bronze_request(".csv")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()

    @contextmanager
    def resolve_local_fixture(resolved_request):
        yield replace(resolved_request, location=str(path))

    monkeypatch.setattr(
        "app.modules.ingestion.readers.file_snapshot.resolve_file_request",
        resolve_local_fixture,
    )
    result = clean_bronze_object(
        request,
        profile(),
        source_system_id="42",
        data_source_id="7",
        dataset_id="11",
        batch_id="job-1",
        expected_sha256=digest,
        expected_rows=1,
    )

    assert result.passed[0].values["email"] == "NULL"
    assert result.passed[0].values["active"] == "false"
    assert not result.issues


@pytest.mark.parametrize(
    ("read_request", "sha256", "rows", "error"),
    [
        (bronze_request(".csv", version=None), "a" * 64, 1, "BRONZE_OBJECT_VERSION_REQUIRED"),
        (bronze_request(".xlsm"), "a" * 64, 1, "BRONZE_FILE_TYPE_MISMATCH"),
        (bronze_request(".csv"), "bad", 1, "BRONZE_OBJECT_CHECKSUM_REQUIRED"),
        (bronze_request(".csv"), "a" * 64, -1, "BRONZE_OBJECT_ROW_COUNT_REQUIRED"),
    ],
)
def test_bronze_object_metadata_is_required(read_request, sha256, rows, error):
    with pytest.raises(ValueError, match=error):
        clean_bronze_object(
            read_request,
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
    request = bronze_request(".csv")

    @contextmanager
    def resolve_local_fixture(resolved_request):
        yield replace(resolved_request, location=str(path))

    monkeypatch.setattr(
        "app.modules.ingestion.readers.file_snapshot.resolve_file_request",
        resolve_local_fixture,
    )
    with pytest.raises(ValueError, match="BRONZE_OBJECT_CHECKSUM_MISMATCH"):
        clean_bronze_object(
            request,
            profile(),
            source_system_id="42",
            data_source_id="7",
            dataset_id="11",
            batch_id="job-1",
            expected_sha256="a" * 64,
            expected_rows=2,
        )


def test_bronze_row_count_mismatch_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / "employee.csv"
    write_fixture(path, ".csv")
    request = bronze_request(".csv")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()

    @contextmanager
    def resolve_local_fixture(resolved_request):
        yield replace(resolved_request, location=str(path))

    monkeypatch.setattr(
        "app.modules.ingestion.readers.file_snapshot.resolve_file_request",
        resolve_local_fixture,
    )
    with pytest.raises(ValueError, match="BRONZE_OBJECT_ROW_COUNT_MISMATCH"):
        clean_bronze_object(
            request,
            profile(),
            source_system_id="42",
            data_source_id="7",
            dataset_id="11",
            batch_id="job-1",
            expected_sha256=digest,
            expected_rows=1,
        )


def test_bronze_object_version_mismatch_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / "employee.csv"
    write_fixture(path, ".csv")
    request = bronze_request(".csv")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()

    @contextmanager
    def resolve_different_version(resolved_request):
        yield replace(
            resolved_request,
            location=str(path),
            object_version_id="bronze-v2",
        )

    monkeypatch.setattr(
        "app.modules.ingestion.readers.file_snapshot.resolve_file_request",
        resolve_different_version,
    )
    with pytest.raises(ValueError, match="BRONZE_OBJECT_VERSION_MISMATCH"):
        clean_bronze_object(
            request,
            profile(),
            source_system_id="42",
            data_source_id="7",
            dataset_id="11",
            batch_id="job-1",
            expected_sha256=digest,
            expected_rows=2,
        )
