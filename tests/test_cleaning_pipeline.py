from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from app.modules.ingestion.contracts import BronzeBatch, FileProvenance, MappingBinding
from app.modules.ingestion.readers.file_snapshot import fingerprint
from app.modules.pipeline.cleaning_pipeline import (
    CleaningNormalizationTask,
    CleaningPipeline,
    CleaningSilverPipeline,
    CleaningTask,
)
from app.modules.pipeline.duplicate_records import DuplicateKeyRule, DuplicateProfile
from app.modules.pipeline.format_validation import (
    FormatFieldRule,
    FormatProfile,
)
from app.modules.pipeline.missing_values import MissingValueProfile, MissingValueRule
from app.modules.pipeline.silver_pipeline import SilverPipeline

if TYPE_CHECKING:
    from app.modules.pipeline.identity_standardization import IdentityStandardizationService
    from app.modules.pipeline.schema_mapping import SchemaMappingService
    from app.modules.pipeline.silver_publication import SilverPublicationService


def batch() -> BronzeBatch:
    provenance = FileProvenance(
        source_system_id="42",
        data_source_id="7",
        dataset_id="11",
        source_type="CSV",
        source_ref="s3://bronze/hr/job-1/employee.csv",
        object_version_id="bronze-v1",
        content_sha256="a" * 64,
        parser_fingerprint="parser",
        columns_fingerprint=fingerprint(["id", "employee_code", "status", "fee"]),
        captured_at="2026-10-19T00:00:00+00:00",
        locator_kind="parsed_record_ordinal",
    )
    columns = ("id", "employee_code", "status", "fee")
    return BronzeBatch(
        batch_id="batch-1",
        domain_id="hr",
        source_type="CSV",
        records=(
            {"id": "1", "employee_code": "NV0001", "status": "Active", "fee": "10.00"},
            {"id": "2", "employee_code": "", "status": "Active", "fee": "11.00"},
            {"id": "3", "employee_code": "NV0003", "status": "unknown", "fee": "12.00"},
            {"id": "4", "employee_code": "NV0004", "status": "Active", "fee": "13.00"},
            {"id": "4", "employee_code": "NV0004", "status": "Inactive", "fee": "14.00"},
        ),
        metadata={"input_version": provenance.input_version},
        mapping_binding=MappingBinding(
            source_system_id="42",
            entity="employee",
            source_schema_version="1.2.0",
            mapping_id="hr-employee",
            mapping_version="1",
            target_schema_version="1.2.0",
        ),
        record_locators=tuple(f"record:{index}" for index in range(1, 6)),
        source_paths=tuple("/" + column for column in columns),
        provenance=provenance,
    )


def task() -> CleaningTask:
    return CleaningTask(
        batch=batch(),
        missing_profile=MissingValueProfile(
            domain="hr",
            entity="employee",
            version="cleaning-1",
            required_source_columns=("employee_code",),
            rules=(MissingValueRule("employee_code", nullable=False),),
        ),
        format_profile=FormatProfile(
            domain="hr",
            entity="employee",
            version="cleaning-1",
            required_source_columns=("employee_code", "status", "fee"),
            rules=(
                FormatFieldRule(
                    field="employee_code",
                    target_field="employee_code",
                    value_kind="string",
                    rule_id="CLN-FMT-01",
                    pattern=r"^NV[0-9]{4}$",
                    max_length=30,
                ),
                FormatFieldRule(
                    field="status",
                    target_field="employee_status",
                    value_kind="string",
                    rule_id="CLN-VAL-01",
                    enum=("Active", "Inactive"),
                ),
                FormatFieldRule(
                    field="fee",
                    target_field="fee",
                    value_kind="decimal",
                    rule_id="CLN-FMT-01",
                    precision=8,
                    scale=2,
                ),
            ),
        ),
        duplicate_profile=DuplicateProfile(
            domain="hr",
            entity="employee",
            version="cleaning-1",
            required_source_columns=("id",),
            key_rules=(DuplicateKeyRule("HR-EMPLOYEE-PK", ("id",), "PRIMARY_KEY"),),
        ),
    )


class ReportCollector:
    def __init__(self) -> None:
        self.reports = []

    def publish(self, report) -> None:
        self.reports.append(report)


def test_cleaning_pipeline_runs_all_stages_and_preserves_count_and_lineage():
    collector = ReportCollector()
    result = CleaningPipeline(collector).clean(task())

    assert result.batch.batch_id == "batch-1"
    assert result.batch.records == (
        {"id": "1", "employee_code": "NV0001", "status": "Active", "fee": "10.00"},
    )
    assert result.batch.record_locators == ("record:1",)
    assert result.batch.provenance is not None
    assert result.batch.provenance == batch().provenance
    assert result.batch.processing_steps == (
        ("CLEAN_MISSING", "cleaning-1"),
        ("CLEAN_FORMAT", "cleaning-1"),
        ("CLEAN_DUPLICATES", "cleaning-1"),
    )
    report = result.report
    assert (report.records_read, report.records_passed, report.records_quarantined) == (
        5,
        1,
        4,
    )
    assert report.records_collapsed == 0
    assert [issue.field for issue in report.missing_issues] == ["employee_code"]
    assert [issue.code for issue in report.format_issues] == ["ENUM_VALUE_INVALID"]
    assert {issue.code for issue in report.duplicate_issues} == {"DUPLICATE_PRIMARY_KEY_CONFLICT"}
    assert collector.reports == [report]


def test_cleaning_count_invariant_accounts_for_approved_duplicate_collapses():
    original = batch()
    duplicate_batch = replace(
        original,
        records=(original.records[0], original.records[0]),
        record_locators=("record:1", "record:2"),
    )
    duplicate_policy = replace(task().duplicate_profile, collapse_exact_primary_key_duplicates=True)
    cleaning_task = replace(
        task(),
        batch=duplicate_batch,
        duplicate_profile=duplicate_policy,
    )

    result = CleaningPipeline().clean(cleaning_task)

    assert len(result.batch.records) == 1
    assert result.report.records_read == 2
    assert result.report.records_passed == 1
    assert result.report.records_quarantined == 0
    assert result.report.records_collapsed == 1


def test_untrusted_bronze_provenance_stops_cleaning_before_any_report():
    original = batch()
    assert original.provenance is not None
    broken_provenance = replace(original.provenance, columns_fingerprint="bad")
    broken_batch = replace(original, provenance=broken_provenance)
    reports = ReportCollector()

    with pytest.raises(ValueError, match="BRONZE_COLUMNS_FINGERPRINT_MISMATCH"):
        CleaningPipeline(reports).clean(replace(task(), batch=broken_batch))

    assert not reports.reports


def test_cleaning_silver_pipeline_passes_only_cleaned_batch_to_standardization():
    observed = {}

    class Mapping:
        def map_batch(self, bronze_batch):
            observed["batch"] = bronze_batch
            return SimpleNamespace(accepted=bronze_batch.records)

    class Types:
        def standardize(self, mapped, **kwargs):
            assert kwargs == {
                "derivation_inputs": None,
                "derivation_input_version": None,
            }
            observed["typed_input"] = mapped.accepted
            return mapped

    class Identity:
        types = Types()

        def standardize(self, typed):
            observed["identity_input"] = typed.accepted
            return SimpleNamespace(accepted=typed.accepted)

    class Publisher:
        def publish(self, results, *, base_snapshot=None):
            assert base_snapshot is None
            observed["published"] = results
            return SimpleNamespace(snapshot_id="snapshot-1")

    pipeline = CleaningSilverPipeline(SilverPipeline(cast("SilverPublicationService", Publisher())))
    result = pipeline.run(
        (
            CleaningNormalizationTask(
                cleaning=task(),
                mapping=cast("SchemaMappingService", Mapping()),
                identity=cast("IdentityStandardizationService", Identity()),
            ),
        )
    )

    assert observed["batch"].records == (
        {"id": "1", "employee_code": "NV0001", "status": "Active", "fee": "10.00"},
    )
    assert observed["batch"].record_locators == ("record:1",)
    assert observed["typed_input"] == observed["identity_input"]
    assert len(observed["published"]) == 1
    assert result.publication.snapshot_id == "snapshot-1"
    assert result.cleaning_reports[0].records_quarantined == 4
