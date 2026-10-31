"""Orchestrate Bronze record cleaning before versioned Standardization."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Protocol
from urllib.parse import urlsplit

from app.modules.ingestion.readers.file_snapshot import fingerprint
from app.modules.pipeline.duplicate_records import (
    DuplicateIssue,
    DuplicateProfile,
    detect_and_handle_duplicates,
)
from app.modules.pipeline.format_validation import (
    FormatIssue,
    FormatProfile,
    apply_format_rules,
)
from app.modules.pipeline.missing_values import (
    MissingValueIssue,
    MissingValueProfile,
    apply_missing_value_rules,
)

if TYPE_CHECKING:
    from app.modules.ingestion.contracts import BronzeBatch
    from app.modules.pipeline.identity_standardization import IdentityStandardizationService
    from app.modules.pipeline.schema_mapping import SchemaMappingService
    from app.modules.pipeline.silver_pipeline import SilverPipeline
    from app.modules.pipeline.silver_publication import PublicationReceipt


CleaningIssue = MissingValueIssue | FormatIssue | DuplicateIssue


@dataclass(frozen=True)
class CleaningTask:
    """Bronze batch and source-bound cleaning policies for one domain entity."""

    batch: BronzeBatch
    missing_profile: MissingValueProfile
    format_profile: FormatProfile
    duplicate_profile: DuplicateProfile


@dataclass(frozen=True)
class CleaningReport:
    domain: str
    entity: str
    batch_id: str
    input_version: str
    records_read: int
    records_passed: int
    records_quarantined: int
    records_collapsed: int
    missing_issues: tuple[MissingValueIssue, ...]
    format_issues: tuple[FormatIssue, ...]
    duplicate_issues: tuple[DuplicateIssue, ...]

    @property
    def issues(self) -> tuple[CleaningIssue, ...]:
        return (*self.missing_issues, *self.format_issues, *self.duplicate_issues)


@dataclass(frozen=True)
class CleaningResult:
    batch: BronzeBatch
    report: CleaningReport


class CleaningReportSink(Protocol):
    """Persist cleaning outcomes/issues in the caller's audit store."""

    def publish(self, report: CleaningReport) -> None: ...


@dataclass(frozen=True)
class CleaningNormalizationTask:
    cleaning: CleaningTask
    mapping: SchemaMappingService
    identity: IdentityStandardizationService
    derivation_inputs: Mapping[str, Mapping[str, object]] | None = None
    derivation_input_version: str | None = None


@dataclass(frozen=True)
class CleaningPublicationResult:
    publication: PublicationReceipt
    cleaning_reports: tuple[CleaningReport, ...]


class CleaningPipeline:
    """Run Missing Values → formats/validation → duplicates on a parsed BronzeBatch."""

    def __init__(self, issue_sink: CleaningReportSink | None = None) -> None:
        self._issue_sink = issue_sink

    def clean(self, task: CleaningTask) -> CleaningResult:
        batch = task.batch
        columns = _validate_batch(task)
        binding = batch.mapping_binding
        if binding is None:
            raise ValueError("MAPPING_BINDING_REQUIRED")
        rows = [tuple(record[column] for column in columns) for record in batch.records]
        provenance = batch.provenance
        if provenance is None:
            raise ValueError("BRONZE_PROVENANCE_REQUIRED")
        if not isinstance(batch.metadata, Mapping):
            raise ValueError("BRONZE_METADATA_REQUIRED")
        input_version = batch.metadata.get("input_version")
        if not isinstance(input_version, str) or not input_version.strip():
            raise ValueError("BRONZE_INPUT_VERSION_REQUIRED")
        if input_version != provenance.input_version:
            raise ValueError("BRONZE_INPUT_VERSION_MISMATCH")
        if (
            provenance.source_type != batch.source_type
            or provenance.source_system_id != binding.source_system_id
        ):
            raise ValueError("BRONZE_PROVENANCE_MISMATCH")
        if provenance.object_version_id is None or not provenance.object_version_id.strip():
            raise ValueError("BRONZE_OBJECT_VERSION_REQUIRED")
        if not _is_s3_object(provenance.source_ref):
            raise ValueError("BRONZE_OBJECT_URI_REQUIRED")
        if not _valid_sha256(provenance.content_sha256):
            raise ValueError("BRONZE_OBJECT_CHECKSUM_REQUIRED")

        missing = apply_missing_value_rules(
            columns=columns,
            rows=rows,
            locators=batch.record_locators,
            profile=task.missing_profile,
            batch_id=batch.batch_id,
            provenance=provenance,
        )
        passed_after_missing = tuple((record.locator, record.values) for record in missing.passed)
        quarantine_by_locator = {record.locator: record.values for record in missing.quarantined}

        formatted = apply_format_rules(
            columns=columns,
            rows=[
                tuple(values[column] for column in columns) for _, values in passed_after_missing
            ],
            locators=tuple(locator for locator, _ in passed_after_missing),
            profile=task.format_profile,
            batch_id=batch.batch_id,
            provenance=provenance,
        )
        for record in formatted.quarantined:
            quarantine_by_locator[record.locator] = record.values

        duplicates = detect_and_handle_duplicates(
            columns=columns,
            rows=[
                tuple(record.values[column] for column in columns) for record in formatted.passed
            ],
            locators=tuple(record.locator for record in formatted.passed),
            profile=task.duplicate_profile,
            batch_id=batch.batch_id,
            provenance=provenance,
        )
        for duplicate_record in duplicates.quarantined:
            quarantine_by_locator[duplicate_record.locator] = duplicate_record.values

        cleaned_batch = replace(
            batch,
            records=tuple(dict(record.values) for record in duplicates.passed),
            record_locators=tuple(record.locator for record in duplicates.passed),
        )
        report = CleaningReport(
            domain=batch.domain_id,
            entity=binding.entity,
            batch_id=batch.batch_id,
            input_version=input_version,
            records_read=len(batch.records),
            records_passed=len(cleaned_batch.records),
            records_quarantined=len(quarantine_by_locator),
            records_collapsed=len(
                {issue.locator for issue in duplicates.issues if issue.disposition == "COLLAPSED"}
            ),
            missing_issues=missing.issues,
            format_issues=formatted.issues,
            duplicate_issues=duplicates.issues,
        )
        if report.records_read != (
            report.records_passed + report.records_quarantined + report.records_collapsed
        ):
            raise ValueError("CLEANING_RECORD_COUNT_INVARIANT_FAILED")
        if self._issue_sink is not None:
            self._issue_sink.publish(report)
        return CleaningResult(cleaned_batch, report)


class CleaningSilverPipeline:
    """Clean input batches, then pass only accepted records through SilverPipeline."""

    def __init__(
        self,
        silver_pipeline: SilverPipeline,
        cleaning_pipeline: CleaningPipeline | None = None,
    ) -> None:
        self._silver_pipeline = silver_pipeline
        self._cleaning_pipeline = cleaning_pipeline or CleaningPipeline()

    def run(
        self,
        tasks: Sequence[CleaningNormalizationTask],
        *,
        base_snapshot: str | None = None,
    ) -> CleaningPublicationResult:
        from app.modules.pipeline.silver_pipeline import NormalizationTask

        normalization_tasks = []
        reports = []
        for task in tasks:
            cleaned = self._cleaning_pipeline.clean(task.cleaning)
            reports.append(cleaned.report)
            normalization_tasks.append(
                NormalizationTask(
                    batch=cleaned.batch,
                    mapping=task.mapping,
                    identity=task.identity,
                    derivation_inputs=task.derivation_inputs,
                    derivation_input_version=task.derivation_input_version,
                )
            )
        publication = self._silver_pipeline.run(
            tuple(normalization_tasks),
            base_snapshot=base_snapshot,
        )
        return CleaningPublicationResult(publication, tuple(reports))


def _validate_batch(task: CleaningTask) -> list[str]:
    batch = task.batch
    if not isinstance(batch.batch_id, str) or not batch.batch_id.strip():
        raise ValueError("INVALID_BATCH_ID")
    if not isinstance(batch.domain_id, str) or not batch.domain_id.strip():
        raise ValueError("INVALID_DOMAIN")
    if batch.source_type.upper() not in {"CSV", "EXCEL"}:
        raise ValueError("UNSUPPORTED_BRONZE_FILE_TYPE")
    binding = batch.mapping_binding
    if binding is None:
        raise ValueError("MAPPING_BINDING_REQUIRED")
    if (
        task.missing_profile.domain != batch.domain_id
        or task.format_profile.domain != batch.domain_id
        or task.duplicate_profile.domain != batch.domain_id
        or task.missing_profile.entity != binding.entity
        or task.format_profile.entity != binding.entity
        or task.duplicate_profile.entity != binding.entity
    ):
        raise ValueError("CLEANING_PROFILE_BINDING_MISMATCH")
    if any(
        not isinstance(locator, str) or not locator.strip() for locator in batch.record_locators
    ):
        raise ValueError("INVALID_SOURCE_LOCATORS")
    if len(batch.record_locators) != len(batch.records) or len(set(batch.record_locators)) != len(
        batch.record_locators
    ):
        raise ValueError("INVALID_SOURCE_LOCATORS")
    if not batch.source_paths:
        raise ValueError("SOURCE_SHAPE_REQUIRED")
    columns = [_decode_top_level_source_path(path) for path in batch.source_paths]
    if len(columns) != len(set(columns)):
        raise ValueError("DUPLICATE_SOURCE_COLUMNS")
    expected_fields = set(columns)
    for record in batch.records:
        if not isinstance(record, Mapping) or set(record) != expected_fields:
            raise ValueError("BRONZE_RECORD_SHAPE_MISMATCH")
    if batch.provenance is None:
        raise ValueError("BRONZE_PROVENANCE_REQUIRED")
    expected_locator_kind = (
        "parsed_record_ordinal" if batch.source_type.upper() == "CSV" else "excel_sheet_row"
    )
    if batch.provenance.locator_kind != expected_locator_kind:
        raise ValueError("UNSUPPORTED_BRONZE_LOCATOR_KIND")
    if batch.provenance.columns_fingerprint != fingerprint(columns):
        raise ValueError("BRONZE_COLUMNS_FINGERPRINT_MISMATCH")
    return columns


def _decode_top_level_source_path(path: str) -> str:
    if not isinstance(path, str) or not path.startswith("/") or "/" in path[1:]:
        raise ValueError("CLEANING_REQUIRES_FLAT_SOURCE_COLUMNS")
    token = path[1:]
    decoded: list[str] = []
    index = 0
    while index < len(token):
        if token[index] != "~":
            decoded.append(token[index])
            index += 1
            continue
        if index + 1 >= len(token) or token[index + 1] not in {"0", "1"}:
            raise ValueError("INVALID_SOURCE_PATH")
        decoded.append("~" if token[index + 1] == "0" else "/")
        index += 2
    column = "".join(decoded)
    if not column.strip():
        raise ValueError("INVALID_SOURCE_PATH")
    return column


def _valid_sha256(value: str) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdefABCDEF" for character in value)
    )


def _is_s3_object(uri: str) -> bool:
    parsed = urlsplit(uri)
    return parsed.scheme.lower() == "s3" and bool(parsed.netloc) and bool(parsed.path.strip("/"))
