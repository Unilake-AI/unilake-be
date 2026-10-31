from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.modules.ingestion.validation import (
    CSVOptions,
    ExcelOptions,
    PostgreSQLOptions,
    ResourceLimits,
    RuleCheck,
    SourceColumn,
    ValidationIssue,
    ValidationPolicy,
    ValidationReport,
)


def policy(**overrides):
    values = {
        "policy_id": "csv-admissions",
        "version": "1",
        "source_system_id": 1,
        "data_source_id": 2,
        "dataset_id": 3,
        "parser": CSVOptions(delimiter=",", encoding="utf-8", has_header=True),
        "columns": (SourceColumn(name="source_id"),),
        "limits": ResourceLimits(
            max_file_bytes=1000,
            max_columns=10,
            max_records=100,
            max_cell_chars=100,
            timeout_seconds=30,
            memory_limit_bytes=10000,
        ),
    }
    values.update(overrides)
    return ValidationPolicy(**values)


def report(**overrides):
    values = {
        "report_id": "report-1",
        "ingestion_job_id": 1,
        "attempt_id": "attempt-1",
        "policy_id": "csv-admissions",
        "policy_version": "1",
        "policy_fingerprint": policy().fingerprint,
        "source_type": "CSV",
        "scan_complete": True,
        "records_scanned": 10,
        "checks": (RuleCheck(rule_id="IN-CSV-004", status="PASS"),),
    }
    values.update(overrides)
    return ValidationReport(**values)


def test_policy_fingerprint_is_stable_and_changes_with_binding_and_rules():
    baseline = policy()
    assert (
        baseline.fingerprint
        == ValidationPolicy.model_validate_json(baseline.model_dump_json()).fingerprint
    )
    assert baseline.fingerprint != policy(dataset_id=4).fingerprint
    assert baseline.fingerprint != policy(allow_empty_records=True).fingerprint
    with pytest.raises(ValidationError, match="frozen"):
        baseline.parser.encoding = "latin-1"


@pytest.mark.parametrize(
    "overrides",
    [
        {"data_source_id": True},
        {"password": "secret"},
        {"allow_empty_records": "false"},
        {"columns": ()},
        {"columns": (SourceColumn(name="id"), SourceColumn(name="id"))},
    ],
)
def test_policy_rejects_invalid_configuration(overrides):
    with pytest.raises(ValidationError):
        policy(**overrides)


@pytest.mark.parametrize(
    "overrides", [{"skip_rows": True}, {"has_header": False}, {"delimiter": '"'}]
)
def test_csv_configuration_rejects_ambiguous_inputs(overrides):
    values = {"delimiter": ",", "encoding": "utf-8", "has_header": True}
    values.update(overrides)
    with pytest.raises(ValidationError):
        CSVOptions(**values)


def test_headerless_csv_requires_exact_positional_contract():
    parser = CSVOptions(
        delimiter=",", encoding="utf-8", has_header=False, positional_columns=("source_id",)
    )
    assert policy(parser=parser).parser == parser
    with pytest.raises(ValidationError, match="POSITIONAL_CONTRACT"):
        policy(parser=parser, allow_extra_columns=True)


@pytest.mark.parametrize(
    ("status", "warnings", "errors", "expected", "accepted"),
    [
        ("PASS", 0, 0, "PASS", True),
        ("WARNING", 1, 0, "WARNING", True),
        ("FAIL", 0, 1, "FAIL", False),
        ("ERROR", 0, 1, "ERROR", False),
        ("NOT_RUN", 0, 0, "ERROR", False),
    ],
)
def test_report_decision_is_derived(status, warnings, errors, expected, accepted):
    result = report(
        checks=(RuleCheck(rule_id="IN-CSV-004", status=status),),
        total_warnings=warnings,
        total_errors=errors,
    )
    assert result.result == expected
    assert result.can_accept is accepted
    assert result.issues_truncated is bool(warnings + errors)


def test_incomplete_scan_cannot_pass_even_without_errors():
    result = report(scan_complete=False, records_scanned=None)
    assert result.result == "ERROR"
    assert not result.can_accept


@pytest.mark.parametrize(
    "overrides",
    [
        {"result": "PASS"},
        {"raw_value": "private"},
        {"records_scanned": None},
        {"total_errors": 1},
        {"checks": ()},
    ],
)
def test_report_rejects_forged_or_inconsistent_results(overrides):
    with pytest.raises(ValidationError):
        report(**overrides)


def test_report_persistence_recomputes_decision():
    original = report(scan_complete=False)
    restored = ValidationReport.model_validate_json(original.model_dump_json(round_trip=True))
    assert restored == original
    assert restored.result == "ERROR"
    assert not restored.can_accept
    assert original.model_dump()["can_accept"] is False


def test_excel_policy_rejects_formula_evaluation_and_invalid_row():
    assert policy(parser=ExcelOptions(sheet_name="Admissions")).parser.kind == "EXCEL"
    with pytest.raises(ValidationError):
        ExcelOptions(sheet_name="Admissions", formula_policy="evaluate")
    with pytest.raises(ValidationError):
        ExcelOptions(sheet_name="Admissions", header_row=0)


def test_postgresql_requires_source_types_and_rejects_credentials():
    parser = PostgreSQLOptions(
        schema_name="public", table_name="students", connection_config_version="v1"
    )
    with pytest.raises(ValidationError, match="POSTGRES_SOURCE_TYPES_REQUIRED"):
        policy(parser=parser)
    assert (
        policy(
            parser=parser,
            columns=(SourceColumn(name="source_id", allowed_source_types=("integer",)),),
        ).parser.kind
        == "POSTGRESQL"
    )
    with pytest.raises(ValidationError):
        PostgreSQLOptions(**{**parser.model_dump(), "password": "secret"})


def test_issue_coordinates_and_totals_must_match_checks():
    issue = ValidationIssue(
        rule_id="IN-CSV-004",
        code="SOURCE_SCHEMA_MISMATCH",
        severity="ERROR",
        row_number=2,
        column_number=1,
    )
    with pytest.raises(ValidationError, match="ISSUE_CHECK_MISMATCH"):
        report(issues=(issue,))
    failed = report(
        checks=(RuleCheck(rule_id="IN-CSV-004", status="FAIL"),),
        issues=(issue,),
        total_errors=1,
    )
    assert not failed.can_accept
    assert not failed.issues_truncated
    with pytest.raises(ValidationError, match="ERROR_COUNT_MISMATCH"):
        report(checks=failed.checks, issues=(issue,), total_errors=0)


def test_required_rule_cannot_be_hidden_by_optional_pass():
    result = report(
        checks=(
            RuleCheck(rule_id="IN-CSV-004", status="NOT_RUN"),
            RuleCheck(rule_id="IN-COM-001", status="PASS", required=False),
        )
    )
    assert not result.can_accept
    with pytest.raises(ValidationError, match="REQUIRED_CHECKS_MISSING"):
        report(checks=(RuleCheck(rule_id="IN-COM-001", status="PASS", required=False),))
