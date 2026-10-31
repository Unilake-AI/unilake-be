from __future__ import annotations

import csv
import hashlib
import json
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import Workbook

from app.modules.ingestion.contracts import FileProvenance
from app.modules.ingestion.readers.contract import ReadRequest
from app.modules.pipeline.format_validation import (
    FormatFieldRule,
    FormatProfile,
    SourceFormatOptions,
    apply_format_rules,
    build_format_profile,
    clean_bronze_formats,
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


def profile() -> FormatProfile:
    return FormatProfile(
        domain="hr",
        entity="employee",
        version="0.1.0-draft",
        required_source_columns=("employee_code", "status", "fee", "start_date", "active"),
        rules=(
            FormatFieldRule(
                field="employee_code",
                target_field="employee_code",
                value_kind="string",
                rule_id="HR-EMPLOYEE-EMPLOYEE_CODE",
                options=SourceFormatOptions(trim=True, unicode_nfc=True),
                pattern=r"^NV[0-9]{4}$",
                max_length=30,
            ),
            FormatFieldRule(
                field="status",
                target_field="employee_status",
                value_kind="string",
                rule_id="HR-EMPLOYEE-EMPLOYEE_STATUS",
                options=SourceFormatOptions(aliases=(("Đang làm", "Active"),)),
                enum=("Active", "Resigned", "Retired"),
            ),
            FormatFieldRule(
                field="fee",
                target_field="fee",
                value_kind="decimal",
                rule_id="HR-EMPLOYEE-FEE",
                options=SourceFormatOptions(decimal_separator=",", thousands_separator="."),
                minimum=Decimal("0"),
                precision=8,
                scale=2,
            ),
            FormatFieldRule(
                field="start_date",
                target_field="start_date",
                value_kind="date",
                rule_id="HR-EMPLOYEE-START_DATE",
                options=SourceFormatOptions(date_formats=("%d/%m/%Y", "%Y-%m-%d")),
            ),
            FormatFieldRule(
                field="active",
                target_field="active",
                value_kind="boolean",
                rule_id="HR-EMPLOYEE-ACTIVE",
                options=SourceFormatOptions(true_tokens=("Có",), false_tokens=("Không",)),
            ),
        ),
    )


def test_approved_format_transformations_and_schema_validation():
    result = apply_format_rules(
        columns=["employee_code", "status", "fee", "start_date", "active"],
        rows=[
            (" NV0001 ", "Đang làm", "1.234,50", "31/12/2026", "Có"),
            ("NV0002", "Unknown", "12,345", "31/02/2026", "Có"),
        ],
        locators=("record:1", "record:2"),
        profile=profile(),
        batch_id="batch-1",
        provenance=provenance(),
    )

    assert len(result.passed) == 1
    assert result.passed[0].values == {
        "employee_code": "NV0001",
        "status": "Active",
        "fee": "1234.50",
        "start_date": "2026-12-31",
        "active": "true",
    }
    assert len(result.quarantined) == 1
    assert result.quarantined[0].values["fee"] == "12,345"
    assert result.quarantined[0].values["start_date"] == "31/02/2026"
    assert {issue.code for issue in result.issues} == {
        "TRIM_APPLIED",
        "APPROVED_ALIAS_MAPPED",
        "DECIMAL_FORMAT_CANONICALIZED",
        "DATE_FORMAT_CANONICALIZED",
        "BOOLEAN_FORMAT_CANONICALIZED",
        "ENUM_VALUE_INVALID",
        "DECIMAL_SCALE_INVALID",
        "DATE_FORMAT_INVALID",
    }
    assert result.invalid_values_by_field == {
        "status": 1,
        "fee": 1,
        "start_date": 1,
    }
    assert result.normalized_values_by_field == {
        "employee_code": 1,
        "status": 1,
        "fee": 1,
        "start_date": 1,
        "active": 1,
    }
    assert "Unknown" not in repr(result.issues)
    assert "12,345" not in repr(result.issues)


def test_excel_date_cells_at_midnight_are_supported_but_time_is_not_discarded():
    date_profile = FormatProfile(
        "hr",
        "employee",
        "test",
        ("hire_date",),
        (
            FormatFieldRule(
                "hire_date",
                "hire_date",
                "date",
                "HR-EMPLOYEE-HIRE-DATE",
                SourceFormatOptions(date_formats=("%Y-%m-%d",)),
            ),
        ),
    )
    result = apply_format_rules(
        columns=["hire_date"],
        rows=[(datetime(2026, 10, 19),)],
        locators=("record:1",),
        profile=date_profile,
        batch_id="batch-1",
        provenance=provenance(),
    )
    assert result.passed[0].values["hire_date"] == "2026-10-19"

    with_time = apply_format_rules(
        columns=["hire_date"],
        rows=[(datetime(2026, 10, 19, 8, 30),)],
        locators=("record:2",),
        profile=date_profile,
        batch_id="batch-1",
        provenance=provenance(),
    )
    assert not with_time.passed
    assert with_time.issues[0].code == "DATE_TIME_COMPONENT_INVALID"


def test_excel_numeric_values_are_validated_and_canonicalized_without_locale_guessing():
    numeric_profile = FormatProfile(
        "academic",
        "course",
        "test",
        ("credits", "fee"),
        (
            FormatFieldRule("credits", "credit_hours", "integer", "CLN-FMT-01"),
            FormatFieldRule(
                "fee",
                "tuition_fee",
                "decimal",
                "CLN-FMT-01",
                precision=12,
                scale=2,
            ),
        ),
    )
    result = apply_format_rules(
        columns=["credits", "fee"],
        rows=[(3.0, 1200.5)],
        locators=("record:1",),
        profile=numeric_profile,
        batch_id="batch-1",
        provenance=provenance(),
    )
    assert result.passed[0].values == {"credits": 3, "fee": "1200.50"}
    assert result.normalized_values_by_field == {"credits": 1, "fee": 1}


def test_only_explicitly_configured_aliases_and_trimming_are_applied():
    result = apply_format_rules(
        columns=["employee_code", "status", "fee", "start_date", "active"],
        rows=[("NV0001", "active", "12,30", "2026-12-31", "yes")],
        locators=("record:1",),
        profile=profile(),
        batch_id="batch-1",
        provenance=provenance(),
    )

    assert not result.passed
    assert len(result.quarantined) == 1
    assert {issue.field for issue in result.issues} == {"status", "active"}
    assert {issue.code for issue in result.issues} == {
        "ENUM_VALUE_INVALID",
        "BOOLEAN_VALUE_INVALID",
    }


def test_missing_values_are_left_for_missing_value_component():
    result = apply_format_rules(
        columns=["employee_code", "status", "fee", "start_date", "active"],
        rows=[("", None, "", None, "")],
        locators=("record:1",),
        profile=profile(),
        batch_id="batch-1",
        provenance=provenance(),
    )

    assert result.passed[0].values == {
        "employee_code": "",
        "status": None,
        "fee": "",
        "start_date": None,
        "active": "",
    }
    assert not result.issues


def test_schema_profile_derives_constraints_with_explicit_source_mapping():
    schema = {
        "file_format": {"date_format": "YYYY-MM-DD"},
        "entities": {
            "employee": {
                "columns": [
                    {
                        "name": "employee_code",
                        "type": "varchar(30)",
                        "pattern": "^NV[0-9]{4}$",
                        "nullable": False,
                    },
                    {
                        "name": "employee_status",
                        "type": "varchar(30)",
                        "enum": ["Active", "Resigned"],
                    },
                    {
                        "name": "hire_date",
                        "type": "date",
                        "nullable": True,
                    },
                    {
                        "name": "salary",
                        "type": "decimal(8,2)",
                        "min": 0,
                        "max": 999999.99,
                    },
                ]
            }
        },
    }
    result = build_format_profile(
        schema,
        domain="hr",
        entity="employee",
        version="test",
        target_to_source={
            "employee_code": "Ma NV",
            "employee_status": "Trang thai",
            "hire_date": "Ngay vao lam",
            "salary": "Luong",
        },
        source_options={
            "employee_status": SourceFormatOptions(aliases=(("Đang làm", "Active"),)),
            "salary": SourceFormatOptions(decimal_separator=","),
        },
        optional_source_fields=("hire_date",),
    )

    assert result.required_source_columns == ("Ma NV", "Trang thai", "Luong")
    assert result.rules[0].pattern == "^NV[0-9]{4}$"
    assert result.rules[0].max_length == 30
    assert result.rules[2].options.date_formats == ("%Y-%m-%d",)
    assert result.rules[3].precision == 8
    assert result.rules[3].scale == 2
    assert result.rules[3].maximum == Decimal("999999.99")
    cleaned = apply_format_rules(
        columns=["Ma NV", "Trang thai", "Luong"],
        rows=[("NV0001", "Active", "1000,00")],
        locators=("record:1",),
        profile=result,
        batch_id="batch-1",
        provenance=provenance(),
    )
    assert len(cleaned.passed) == 1
    assert cleaned.passed[0].values["Luong"] == "1000.00"


def test_profiles_build_for_all_owned_domain_entities():
    count = 0
    for domain in ("admissions", "academic", "hr"):
        schema = json.loads(
            (ROOT / f"docs/data-schema/{domain}_v1.schema.json").read_text(encoding="utf-8")
        )
        for entity, entity_schema in schema["entities"].items():
            if not entity_schema.get("owned_by_dataset") or not entity_schema.get("columns"):
                continue
            target_fields = tuple(column["name"] for column in entity_schema["columns"])
            source_options = {
                column["name"]: SourceFormatOptions(timestamp_formats=("%Y-%m-%dT%H:%M:%S%z",))
                for column in entity_schema["columns"]
                if str(column.get("type", "")).lower().startswith("timestamp")
            }
            build_format_profile(
                schema,
                domain=domain,
                entity=entity,
                version="test",
                target_to_source={field: field for field in target_fields},
                source_options=source_options,
            )
            count += 1
    assert count == 22


def test_timestamp_is_normalized_to_utc_and_naive_value_is_rejected():
    timestamp_profile = FormatProfile(
        "hr",
        "employee",
        "test",
        ("timestamp",),
        (
            FormatFieldRule(
                "timestamp",
                "created_at",
                "timestamp",
                "HR-EMPLOYEE-CREATED-AT",
                SourceFormatOptions(timestamp_formats=("%Y-%m-%dT%H:%M:%S%z",)),
            ),
        ),
    )
    result = apply_format_rules(
        columns=["timestamp"],
        rows=[("2026-12-31T07:00:00+07:00",)],
        locators=("record:1",),
        profile=timestamp_profile,
        batch_id="batch-1",
        provenance=provenance(),
    )
    assert result.passed[0].values["timestamp"] == "2026-12-31T00:00:00Z"
    naive_result = apply_format_rules(
        columns=["timestamp"],
        rows=[(datetime(2026, 12, 31, 7),)],
        locators=("record:2",),
        profile=timestamp_profile,
        batch_id="batch-1",
        provenance=provenance(),
    )
    assert not naive_result.passed
    assert naive_result.issues[0].code == "TIMESTAMP_TIMEZONE_REQUIRED"


def bronze_request(extension: str) -> ReadRequest:
    source_type = "EXCEL" if extension == ".xlsx" else "CSV"
    config = (
        {"sheet_name": "Data", "header_row": 1, "skip_rows": 0}
        if source_type == "EXCEL"
        else {"delimiter": ",", "encoding": "utf-8", "has_header": True}
    )
    return ReadRequest(
        source_type,
        f"s3://bronze/hr/job-1/employee{extension}",
        config,
        f"employee{extension}",
        "bronze-v1",
    )


def write_fixture(path: Path, extension: str) -> None:
    records = [("employee_code", "employee_status"), (" NV0001 ", "Active")]
    if extension == ".csv":
        with path.open("w", encoding="utf-8", newline="") as stream:
            csv.writer(stream).writerows(records)
        return
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Data"
    for row in records:
        sheet.append(row)
    workbook.save(path)
    workbook.close()


@pytest.mark.parametrize("extension", [".csv", ".xlsx"])
def test_clean_bronze_formats_reads_pinned_csv_and_xlsx(tmp_path, monkeypatch, extension):
    path = tmp_path / f"employee{extension}"
    write_fixture(path, extension)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    source_profile = FormatProfile(
        "hr",
        "employee",
        "test",
        ("employee_code", "employee_status"),
        (
            FormatFieldRule(
                "employee_code",
                "employee_code",
                "string",
                "HR-EMPLOYEE-EMPLOYEE-CODE",
                SourceFormatOptions(trim=True),
                pattern=r"^NV[0-9]{4}$",
            ),
            FormatFieldRule(
                "employee_status",
                "employee_status",
                "string",
                "HR-EMPLOYEE-EMPLOYEE-STATUS",
                enum=("Active", "Inactive"),
            ),
        ),
    )

    @contextmanager
    def resolve_local_fixture(request):
        assert request.object_version_id == "bronze-v1"
        yield replace(request, location=str(path))

    monkeypatch.setattr(
        "app.modules.ingestion.readers.file_snapshot.resolve_file_request",
        resolve_local_fixture,
    )
    result = clean_bronze_formats(
        bronze_request(extension),
        source_profile,
        source_system_id="42",
        data_source_id="7",
        dataset_id="11",
        batch_id="job-1",
        expected_sha256=digest,
        expected_rows=1,
    )

    assert len(result.passed) == 1
    assert result.passed[0].values["employee_code"] == "NV0001"
    expected_locator = "record:1" if extension == ".csv" else "sheet:Data:row:2"
    assert result.passed[0].locator == expected_locator
    assert result.provenance.content_sha256 == digest


def test_missing_source_column_is_a_structure_error():
    with pytest.raises(ValueError, match=r"REQUIRED_SOURCE_FIELD_MISSING:.*employee_code"):
        apply_format_rules(
            columns=["status"],
            rows=[("Active",)],
            locators=("record:1",),
            profile=profile(),
            batch_id="batch-1",
            provenance=provenance(),
        )
