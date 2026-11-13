"""Đối chiếu bộ làm sạch DuckDB với quy tắc và kết quả của luồng trước."""

from __future__ import annotations

import csv
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.modules.ingestion.contracts import BronzeBatch, FileProvenance, MappingBinding
from app.modules.ingestion.contracts.readers import ReadRequest
from app.modules.pipeline.cleaning.contracts import CleaningReport, CleaningResult
from app.modules.pipeline.cleaning.duckdb_engine import DuckDBCleaningEngine
from app.modules.pipeline.cleaning.duplicate_records import (
    DuplicateKeyRule,
    DuplicateProfile,
    detect_and_handle_duplicates,
)
from app.modules.pipeline.cleaning.format_validation import (
    FormatFieldRule,
    FormatProfile,
    SourceFormatOptions,
    apply_format_rules,
)
from app.modules.pipeline.cleaning.missing_values import (
    MissingValueProfile,
    MissingValueRule,
    apply_missing_value_rules,
)
from app.modules.pipeline.cleaning.pipeline import CleaningPipeline, CleaningTask
from app.shared.fingerprints import fingerprint


def _make_provenance(columns: list[str]) -> FileProvenance:
    return FileProvenance(
        source_system_id="SYS_TEST",
        data_source_id="test-source",
        dataset_id="student",
        source_type="CSV",
        source_ref="s3://bronze/test/data.csv",
        content_sha256="a" * 64,
        parser_fingerprint="parser-v1",
        columns_fingerprint=fingerprint(columns),
        captured_at="2026-10-19T00:00:00+00:00",
        object_version_id="obj_v1",
        locator_kind="parsed_record_ordinal",
    )


def _make_task(
    *,
    columns: list[str],
    records: list[dict[str, object]],
    locators: list[str],
    missing_rules: list[MissingValueRule] | None = None,
    format_rules: list[FormatFieldRule] | None = None,
    duplicate_rules: list[DuplicateKeyRule] | None = None,
    collapse_exact: bool = False,
) -> CleaningTask:
    prov = _make_provenance(columns)
    batch = BronzeBatch(
        batch_id="batch-001",
        domain_id="admissions",
        source_type="CSV",
        source_paths=tuple(f"/{col}" for col in columns),
        record_locators=tuple(locators),
        records=tuple(records),
        processing_steps=(),
        provenance=prov,
        mapping_binding=MappingBinding(
            source_system_id="SYS_TEST",
            entity="student",
            source_schema_version="v1.0",
            mapping_id="map-01",
            mapping_version="v1.0",
            target_schema_version="v1.0",
        ),
        metadata={"input_version": prov.input_version},
    )
    missing_prof = MissingValueProfile(
        domain="admissions",
        entity="student",
        version="v1.0",
        required_source_columns=tuple(columns),
        rules=tuple(
            missing_rules
            if missing_rules is not None
            else [MissingValueRule(field=col, nullable=True) for col in columns]
        ),
    )
    fmt_rules = (
        format_rules
        if format_rules is not None
        else [
            FormatFieldRule(
                field=col,
                target_field=col,
                value_kind="string",
                rule_id=f"FMT-{col}",
            )
            for col in columns
        ]
    )
    format_prof = FormatProfile(
        domain="admissions",
        entity="student",
        version="v1.0",
        required_source_columns=tuple(r.field for r in fmt_rules if r.required_in_source),
        rules=tuple(fmt_rules),
    )
    dup_rules = duplicate_rules or [
        DuplicateKeyRule(rule_id="PK-01", fields=(columns[0],), kind="PRIMARY_KEY")
    ]
    duplicate_prof = DuplicateProfile(
        domain="admissions",
        entity="student",
        version="v1.0",
        required_source_columns=tuple(columns),
        key_rules=tuple(dup_rules),
        collapse_exact_primary_key_duplicates=collapse_exact,
    )
    return CleaningTask(
        batch=batch,
        missing_profile=missing_prof,
        format_profile=format_prof,
        duplicate_profile=duplicate_prof,
    )


def test_duckdb_read_csv_file(tmp_path: Path):
    engine = DuckDBCleaningEngine()
    csv_path = tmp_path / "sample.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "name", "score"])
        writer.writerow(["1", "Nguyen Van A", "95"])
        writer.writerow(["2", "Tran Thi B", "88"])

    cols, rows, locs = engine.read_csv_file(csv_path)
    assert cols == ["id", "name", "score"]
    assert len(rows) == 2
    assert rows[0] == ("1", "Nguyen Van A", "95")
    assert rows[1] == ("2", "Tran Thi B", "88")
    assert locs == ("record:1", "record:2")


def test_duckdb_missing_values_preserves_nullable_and_quarantines_required():
    engine = DuckDBCleaningEngine()
    columns = ["id", "name", "email"]
    rows: list[tuple[object, ...]] = [
        ("1", "Alice", "alice@example.com"),
        ("2", "", "bob@example.com"),  # Thiếu tên bắt buộc: tách riêng dòng để kiểm tra.
        ("3", "Charlie", ""),  # Email được phép trống: lưu thành giá trị rỗng.
    ]
    locators = ("loc:1", "loc:2", "loc:3")
    prov = _make_provenance(columns)

    profile = MissingValueProfile(
        domain="admissions",
        entity="student",
        version="v1.0",
        required_source_columns=("id", "name", "email"),
        rules=(
            MissingValueRule(field="name", nullable=False, null_tokens=("",)),
            MissingValueRule(field="email", nullable=True, null_tokens=("",)),
        ),
    )

    res = engine.process_missing_values(
        columns=columns,
        rows=rows,
        locators=locators,
        profile=profile,
        batch_id="batch-01",
        provenance=prov,
    )

    assert res.records_read == 3
    assert len(res.passed) == 2
    assert len(res.quarantined) == 1
    assert res.quarantined[0].locator == "loc:2"
    assert res.passed[1].locator == "loc:3"
    assert res.passed[1].values["email"] is None

    # Kiểm tra các vấn đề được ghi nhận.
    assert any(i.code == "REQUIRED_VALUE_MISSING" and i.locator == "loc:2" for i in res.issues)
    assert any(i.code == "NULL_VALUE_PRESERVED" and i.locator == "loc:3" for i in res.issues)
    assert res.missing_values_by_field["name"] == 1
    assert res.missing_values_by_field["email"] == 1


def test_duckdb_format_validation_and_normalization():
    engine = DuckDBCleaningEngine()
    columns = ["status", "score", "active"]
    rows: list[tuple[object, ...]] = [
        ("  active  ", "85", "true"),
        ("pending", "120", "false"),  # Điểm lớn hơn 100: tách riêng dòng để kiểm tra.
    ]
    locators = ("loc:1", "loc:2")
    prov = _make_provenance(columns)

    rules = (
        FormatFieldRule(
            field="status",
            target_field="status",
            value_kind="string",
            rule_id="CLN-FMT-01",
            options=SourceFormatOptions(trim=True),
        ),
        FormatFieldRule(
            field="score",
            target_field="score",
            value_kind="integer",
            rule_id="CLN-FMT-02",
            maximum=Decimal(100),
        ),
        FormatFieldRule(
            field="active",
            target_field="active",
            value_kind="boolean",
            rule_id="CLN-FMT-03",
        ),
    )
    profile = FormatProfile(
        domain="admissions",
        entity="student",
        version="v1.0",
        required_source_columns=("status", "score", "active"),
        rules=rules,
    )

    res = engine.process_format_validation(
        columns=columns,
        rows=rows,
        locators=locators,
        profile=profile,
        batch_id="batch-01",
        provenance=prov,
    )

    assert len(res.passed) == 1
    assert len(res.quarantined) == 1
    assert res.passed[0].locator == "loc:1"
    assert res.passed[0].values["status"] == "active"  # Đã bỏ khoảng trắng theo quy định.
    assert res.quarantined[0].locator == "loc:2"
    assert any(i.code == "VALUE_ABOVE_MAXIMUM" and i.locator == "loc:2" for i in res.issues)
    assert any(i.code == "TRIM_APPLIED" and i.locator == "loc:1" for i in res.issues)


def test_duckdb_duplicate_detection_and_collapsing():
    engine = DuckDBCleaningEngine()
    columns = ["id", "code", "name"]
    rows: list[tuple[object, ...]] = [
        ("1", "A01", "Alice"),
        ("1", "A01", "Alice"),  # Trùng hoàn toàn với dòng đầu.
        ("2", "A02", "Bob"),
        ("3", "A02", "Charlie"),  # Trùng mã A02 nhưng nội dung khác nhau.
    ]
    locators = ("loc:1", "loc:2", "loc:3", "loc:4")
    prov = _make_provenance(columns)

    profile = DuplicateProfile(
        domain="admissions",
        entity="student",
        version="v1.0",
        required_source_columns=tuple(columns),
        key_rules=(
            DuplicateKeyRule(rule_id="PK", fields=("id",), kind="PRIMARY_KEY"),
            DuplicateKeyRule(rule_id="UK", fields=("code",), kind="UNIQUE"),
        ),
        collapse_exact_primary_key_duplicates=True,
    )

    res = engine.process_duplicates(
        columns=columns,
        rows=rows,
        locators=locators,
        profile=profile,
        batch_id="batch-01",
        provenance=prov,
    )

    assert res.records_read == 4
    assert len(res.passed) == 1
    assert res.passed[0].locator == "loc:1"
    assert res.passed[0].source_locators == ("loc:1", "loc:2")
    assert len(res.quarantined) == 2
    assert {r.locator for r in res.quarantined} == {"loc:3", "loc:4"}
    assert any(i.code == "EXACT_DUPLICATE_COLLAPSED" and i.locator == "loc:2" for i in res.issues)
    assert any(i.code == "DUPLICATE_KEY_COLLISION" for i in res.issues)


def test_duckdb_clean_pipeline_full_workflow():
    columns = ["student_id", "email", "age", "status"]
    records: list[dict[str, object]] = [
        {"student_id": "S01", "email": "s01@uni.edu", "age": "20", "status": "active"},
        {
            "student_id": "S01",
            "email": "s01@uni.edu",
            "age": "20",
            "status": "active",
        },  # Dòng trùng hoàn toàn được gộp khi quy định cho phép.
        {
            "student_id": "S02",
            "email": "",
            "age": "21",
            "status": "active",
        },  # Email được phép trống.
        {
            "student_id": "S03",
            "email": "s03@uni.edu",
            "age": "not-a-number",
            "status": "active",
        },  # Sai định dạng: tách riêng dòng để kiểm tra.
    ]
    locators = ["L1", "L2", "L3", "L4"]

    task = _make_task(
        columns=columns,
        records=records,
        locators=locators,
        missing_rules=[
            MissingValueRule(field="email", nullable=True, null_tokens=("",)),
            MissingValueRule(field="student_id", nullable=False, null_tokens=("",)),
        ],
        format_rules=[
            FormatFieldRule(
                field="student_id",
                target_field="student_id",
                value_kind="string",
                rule_id="CLN-ID",
            ),
            FormatFieldRule(
                field="email",
                target_field="email",
                value_kind="string",
                rule_id="CLN-EMAIL",
            ),
            FormatFieldRule(
                field="age",
                target_field="age",
                value_kind="integer",
                rule_id="CLN-AGE",
            ),
            FormatFieldRule(
                field="status",
                target_field="status",
                value_kind="string",
                rule_id="CLN-STATUS",
            ),
        ],
        duplicate_rules=[
            DuplicateKeyRule(rule_id="PK", fields=("student_id",), kind="PRIMARY_KEY"),
        ],
        collapse_exact=True,
    )

    engine = DuckDBCleaningEngine()
    result = engine.clean(task)

    # Kiểm tra tổng số dòng được giữ nguyên.
    rep = result.report
    assert rep.records_read == 4
    assert rep.records_passed == 2  # Hai dòng đạt yêu cầu.
    assert rep.records_quarantined == 1  # Một dòng bị cách ly.
    assert rep.records_collapsed == 1  # Một dòng trùng đã được gộp.
    assert rep.records_read == rep.records_passed + rep.records_quarantined + rep.records_collapsed

    assert len(result.batch.records) == 2
    assert result.batch.records[0]["student_id"] == "S01"
    assert result.batch.records[1]["student_id"] == "S02"
    assert result.batch.records[1]["email"] is None  # Ô được phép trống.

    assert result.quarantined_locators == ("L4",)


def test_cleaning_pipeline_uses_duckdb_engine_transparently():
    columns = ["id", "name"]
    records: list[dict[str, object]] = [{"id": "1", "name": "Test"}]
    locators = ["R1"]
    task = _make_task(columns=columns, records=records, locators=locators)

    pipeline = CleaningPipeline()
    res = pipeline.clean(task)
    assert res.report.records_read == 1
    assert res.report.records_passed == 1
    assert res.report.records_quarantined == 0


@pytest.mark.parametrize("trim", [False, True])
@pytest.mark.parametrize("nullable", [False, True])
@pytest.mark.parametrize("tokens", [(), ("",), ("", "NULL", "7")])
def test_missing_result_matches_reference_including_unicode_whitespace(trim, nullable, tokens):
    values = [None, "", " ", "\tNULL\n", "\u00a0NULL\u3000", "NULL", "7", 7, False, Decimal("7")]
    columns = ["_locator", "ID", "id"]
    profile = MissingValueProfile(
        "hr",
        "employee",
        "missing-42",
        tuple(columns),
        (MissingValueRule("ID", nullable, tokens, trim), MissingValueRule("id", True)),
    )
    kwargs: dict[str, Any] = {
        "columns": columns,
        "rows": [("x", value, None) for value in values],
        "locators": tuple(f"row:{i}" for i in range(len(values))),
        "profile": profile,
        "batch_id": "parity",
        "provenance": _make_provenance(columns),
    }
    assert DuckDBCleaningEngine().process_missing_values(**kwargs) == apply_missing_value_rules(
        **kwargs
    )


@pytest.mark.parametrize("collapse", [False, True])
@pytest.mark.parametrize(
    "rows",
    [
        [(" ", "a"), (" ", "b"), ("\t", "c"), (None, "d"), ("", "e")],
        [(7, "a"), ("7", "b"), ("007", "c")],
        [(7, "a"), (7.0, "b"), (Decimal("7"), "c"), (True, "d"), (1, "e")],
        [("1", "a"), ("1", "a"), ("2", "b"), ("3", "b")],
        [("1", "a"), ("1", "a"), ("2", "a")],
        [],
    ],
)
def test_duplicate_result_matches_reference_for_types_empty_keys_and_overlapping_rules(
    rows, collapse
):
    columns = ["ID", "id"]
    profile = DuplicateProfile(
        "hr",
        "employee",
        "duplicates-3",
        tuple(columns),
        (
            DuplicateKeyRule("PK", ("ID",), "PRIMARY_KEY"),
            DuplicateKeyRule("UK", ("id",), "UNIQUE"),
            DuplicateKeyRule("BOTH", tuple(columns), "COMPOSITE_UNIQUE"),
        ),
        collapse,
    )
    kwargs: dict[str, Any] = {
        "columns": columns,
        "rows": rows,
        "locators": tuple(f"row:{i}" for i in range(len(rows))),
        "profile": profile,
        "batch_id": "parity",
        "provenance": _make_provenance(columns),
    }
    assert DuckDBCleaningEngine().process_duplicates(**kwargs) == detect_and_handle_duplicates(
        **kwargs
    )


def test_all_format_kinds_preserve_results_errors_versions_and_order():
    columns = ["id", "name", "number", "fee", "day", "when", "active"]
    rules = (
        FormatFieldRule("id", "id", "integer", "ID", preserve_source_id=True),
        FormatFieldRule(
            "name",
            "name",
            "string",
            "TEXT",
            SourceFormatOptions(trim=True, unicode_nfc=True),
            pattern="^[A-Z]+$",
            max_length=4,
        ),
        FormatFieldRule(
            "number", "number", "integer", "INT", minimum=Decimal(0), maximum=Decimal(100)
        ),
        FormatFieldRule(
            "fee",
            "fee",
            "decimal",
            "FEE",
            SourceFormatOptions(decimal_separator=",", thousands_separator="."),
            precision=6,
            scale=2,
        ),
        FormatFieldRule(
            "day", "day", "date", "DAY", SourceFormatOptions(date_formats=("%d/%m/%Y", "%Y-%m-%d"))
        ),
        FormatFieldRule(
            "when",
            "when",
            "timestamp",
            "TIME",
            SourceFormatOptions(timestamp_formats=("%Y-%m-%dT%H:%M:%S%z",)),
        ),
        FormatFieldRule(
            "active",
            "active",
            "boolean",
            "BOOL",
            SourceFormatOptions(true_tokens=("Có",), false_tokens=("Không",)),
        ),
    )
    good: tuple[object, ...] = (
        "007",
        " AB ",
        3.0,
        "1.234,50",
        "31/12/2026",
        "2026-12-31T07:00:00+07:00",
        "Có",
    )
    rows: list[tuple[object, ...]] = [
        good,
        (
            "7",
            "AB",
            "4",
            Decimal("12.30"),
            date(2026, 1, 1),
            datetime(2026, 1, 1, tzinfo=UTC),
            True,
        ),
    ]
    invalid_values = [7.0, "abcde", "3.5", "12,345", "31/02/2026", "2026-12-31T07:00:00", "yes"]
    for index, value in enumerate(invalid_values):
        row = list(good)
        row[index] = value
        rows.append(tuple(row))
    # Một dòng có nhiều lỗi: giữ thứ tự quy định và không báo sửa thành công trên dòng bị loại.
    rows.extend([tuple(invalid_values), tuple(None for _ in columns), tuple("" for _ in columns)])
    kwargs: dict[str, Any] = {
        "columns": columns,
        "rows": rows,
        "locators": tuple(f"sheet:Data:row:{i + 2}" for i in range(len(rows))),
        "profile": FormatProfile("hr", "employee", "format-72", tuple(columns), rules),
        "batch_id": "parity",
        "provenance": _make_provenance(columns),
    }
    assert DuckDBCleaningEngine().process_format_validation(**kwargs) == apply_format_rules(
        **kwargs
    )


@pytest.mark.parametrize(
    "columns", [["_row_idx", "_locator"], ["ID", "id"], ['x"; DROP TABLE t;--', "x/y~z"]]
)
def test_source_column_names_do_not_conflict_with_internal_tables(columns):
    task = _make_task(
        columns=columns, records=[dict.fromkeys(columns, "007")], locators=["original:42"]
    )
    task = replace(
        task,
        batch=replace(
            task.batch,
            source_paths=tuple(
                "/" + name.replace("~", "~0").replace("/", "~1") for name in columns
            ),
        ),
    )
    result = CleaningPipeline().clean(task)
    assert result.batch.records == task.batch.records
    assert result.batch.record_locators == ("original:42",)
    assert result.batch.provenance == task.batch.provenance


def _reference_clean(task: CleaningTask) -> CleaningResult:
    """Ghép ba bước cũ thành kết quả tham chiếu, không gọi bộ xử lý DuckDB mới."""
    batch = task.batch
    columns = list(batch.records[0]) if batch.records else [path[1:] for path in batch.source_paths]
    kwargs: dict[str, Any] = {
        "columns": columns,
        "batch_id": batch.batch_id,
        "provenance": batch.provenance,
    }
    missing = apply_missing_value_rules(
        **kwargs,
        rows=[tuple(row[c] for c in columns) for row in batch.records],
        locators=batch.record_locators,
        profile=task.missing_profile,
    )
    formatted = apply_format_rules(
        **kwargs,
        rows=[tuple(row.values[c] for c in columns) for row in missing.passed],
        locators=tuple(row.locator for row in missing.passed),
        profile=task.format_profile,
    )
    duplicates = detect_and_handle_duplicates(
        **kwargs,
        rows=[tuple(row.values[c] for c in columns) for row in formatted.passed],
        locators=tuple(row.locator for row in formatted.passed),
        profile=task.duplicate_profile,
    )
    quarantined = tuple(
        row.locator for stage in (missing, formatted, duplicates) for row in stage.quarantined
    )
    assert batch.provenance is not None
    return CleaningResult(
        replace(
            batch,
            records=tuple(dict(row.values) for row in duplicates.passed),
            record_locators=tuple(row.locator for row in duplicates.passed),
            processing_steps=(
                *batch.processing_steps,
                ("CLEAN_MISSING", task.missing_profile.version),
                ("CLEAN_FORMAT", task.format_profile.version),
                ("CLEAN_DUPLICATES", task.duplicate_profile.version),
            ),
        ),
        CleaningReport(
            batch.domain_id,
            task.missing_profile.entity,
            batch.batch_id,
            batch.provenance.input_version,
            len(batch.records),
            len(duplicates.passed),
            len(quarantined),
            sum(issue.disposition == "COLLAPSED" for issue in duplicates.issues),
            missing.issues,
            formatted.issues,
            duplicates.issues,
        ),
        quarantined,
    )


@pytest.mark.parametrize("collapse", [False, True])
@pytest.mark.parametrize("empty", [False, True])
def test_full_pipeline_equals_reference_including_report_and_provenance(collapse, empty):
    records: list[dict[str, object]] = (
        [
            {"id": "007", "name": " AB "},
            {"id": "7", "name": "CD"},
            {"id": "8", "name": "EF"},
            {"id": "8", "name": "EF"},
            {"id": "9", "name": ""},
            {"id": "10", "name": "too long"},
        ]
        if not empty
        else []
    )
    task = _make_task(
        columns=["id", "name"],
        records=records,
        locators=[f"line:{i}" for i in range(len(records))],
        missing_rules=[MissingValueRule("name", False)],
        format_rules=[
            FormatFieldRule("id", "id", "integer", "ID", preserve_source_id=True),
            FormatFieldRule(
                "name", "name", "string", "TEXT", SourceFormatOptions(trim=True), max_length=4
            ),
        ],
        collapse_exact=collapse,
    )
    task = replace(
        task,
        missing_profile=replace(task.missing_profile, version="missing-1"),
        format_profile=replace(task.format_profile, version="format-2"),
        duplicate_profile=replace(task.duplicate_profile, version="duplicate-3"),
    )
    assert CleaningPipeline().clean(task) == _reference_clean(task)


@pytest.mark.parametrize("version", [None, "", "null"])
def test_read_bronze_requires_pinned_version_before_reading(version):
    profile = MissingValueProfile("hr", "employee", "1", ("id",), (MissingValueRule("id", False),))
    with pytest.raises(ValueError, match="BRONZE_OBJECT_VERSION_REQUIRED"):
        DuckDBCleaningEngine().clean_bronze_object(
            ReadRequest("CSV", "s3://bronze/data.csv", {}, "data.csv", version),
            profile,
            source_system_id="1",
            data_source_id="1",
            dataset_id="1",
            batch_id="b",
            expected_sha256="a" * 64,
            expected_rows=1,
        )


def test_runtime_configuration_is_not_silently_ignored():
    with pytest.raises(ValueError, match="INVALID_DUCKDB_THREADS"):
        DuckDBCleaningEngine(threads=0).connect()
    with pytest.raises(Exception, match=r"memory|Memory|unit"):
        DuckDBCleaningEngine(memory_limit="invalid").connect()


def test_connection_is_closed_when_a_cleaning_step_fails(monkeypatch):
    engine = DuckDBCleaningEngine()
    connection = engine.connect()
    monkeypatch.setattr(engine, "connect", lambda: connection)

    def fail(**_kwargs):
        raise RuntimeError("format-step-failed")

    monkeypatch.setattr(engine, "process_format_validation", fail)
    task = _make_task(columns=["id"], records=[{"id": "007"}], locators=["row:1"])
    with pytest.raises(RuntimeError, match="format-step-failed"):
        engine.clean(task)
    with pytest.raises(Exception, match="closed"):
        connection.execute("SELECT 1")


def test_caller_owned_connection_remains_open():
    engine = DuckDBCleaningEngine()
    connection = engine.connect()
    try:
        columns = ["id"]
        engine.process_missing_values(
            columns=columns,
            rows=[("007",)],
            locators=("row:1",),
            profile=MissingValueProfile(
                "hr", "employee", "1", ("id",), (MissingValueRule("id", False),)
            ),
            batch_id="b",
            provenance=_make_provenance(columns),
            conn=connection,
        )
        assert connection.execute("SELECT 1").fetchall() == [(1,)]
    finally:
        connection.close()
