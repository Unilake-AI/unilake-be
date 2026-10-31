"""Read pinned Bronze CSV/XLSX objects and apply field-level missing-value rules."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

from app.modules.ingestion.contracts import FileProvenance
from app.modules.ingestion.readers.contract import ReadRequest
from app.modules.ingestion.readers.factory import SourceReaderFactory
from app.modules.ingestion.readers.file_snapshot import read_file_snapshot

IssueSeverity = Literal["INFO", "ERROR"]
Disposition = Literal["PRESERVED", "QUARANTINED"]


@dataclass(frozen=True)
class MissingValueRule:
    """Rule for one parsed source column; tokens are exact unless trimming is enabled."""

    field: str
    nullable: bool
    null_tokens: tuple[str, ...] = ("",)
    trim_for_token_match: bool = False
    rule_id: str = "CLN-NULL-01"
    required_in_source: bool = True


@dataclass(frozen=True)
class MissingValueProfile:
    """Versioned source-column policy for one bound domain entity."""

    domain: str
    entity: str
    version: str
    required_source_columns: tuple[str, ...]
    rules: tuple[MissingValueRule, ...]


@dataclass(frozen=True)
class CleanedRecord:
    locator: str
    values: Mapping[str, object]


@dataclass(frozen=True)
class QuarantinedRecord:
    locator: str
    values: Mapping[str, object]


@dataclass(frozen=True)
class MissingValueIssue:
    rule_id: str
    rule_version: str
    domain: str
    entity: str
    field: str
    code: str
    severity: IssueSeverity
    disposition: Disposition
    locator: str


@dataclass(frozen=True)
class MissingValueResult:
    domain: str
    entity: str
    batch_id: str
    records_read: int
    passed: tuple[CleanedRecord, ...]
    quarantined: tuple[QuarantinedRecord, ...]
    issues: tuple[MissingValueIssue, ...]
    missing_values_by_field: Mapping[str, int]
    provenance: FileProvenance


def clean_bronze_object(
    request: ReadRequest,
    profile: MissingValueProfile,
    *,
    source_system_id: str,
    data_source_id: str,
    dataset_id: str,
    batch_id: str,
    expected_sha256: str,
    expected_rows: int,
) -> MissingValueResult:
    """Read one exact Bronze object version, parse CSV/XLSX, then apply missing rules.

    The request must point to an S3/MinIO object and carry its non-null version ID.
    ``expected_sha256`` and ``expected_rows`` should come from the successful
    ingestion job. Original Bronze bytes are never changed.
    """
    _validate_context(
        request,
        profile,
        source_system_id=source_system_id,
        data_source_id=data_source_id,
        dataset_id=dataset_id,
        batch_id=batch_id,
        expected_sha256=expected_sha256,
        expected_rows=expected_rows,
    )

    reader = SourceReaderFactory.get_reader(request.source_type)
    result = read_file_snapshot(
        request,
        reader,
        source_system_id=source_system_id,
        data_source_id=data_source_id,
        dataset_id=dataset_id,
    )
    provenance = result.provenance
    if provenance is None:
        raise ValueError("BRONZE_PROVENANCE_REQUIRED")
    if provenance.object_version_id != request.object_version_id:
        raise ValueError("BRONZE_OBJECT_VERSION_MISMATCH")
    if provenance.content_sha256.lower() != expected_sha256.lower():
        raise ValueError("BRONZE_OBJECT_CHECKSUM_MISMATCH")
    if result.rows_read != expected_rows:
        raise ValueError("BRONZE_OBJECT_ROW_COUNT_MISMATCH")

    return apply_missing_value_rules(
        columns=result.columns,
        rows=result.rows,
        locators=result.record_locators,
        profile=profile,
        batch_id=batch_id,
        provenance=provenance,
    )


def apply_missing_value_rules(
    *,
    columns: list[str],
    rows: list[tuple[object, ...]],
    locators: tuple[str, ...],
    profile: MissingValueProfile,
    batch_id: str,
    provenance: FileProvenance,
) -> MissingValueResult:
    """Apply an already parsed source profile without changing non-missing values."""
    _validate_profile(profile)
    if not isinstance(batch_id, str) or not batch_id.strip():
        raise ValueError("INVALID_BATCH_ID")
    if not isinstance(provenance, FileProvenance):
        raise ValueError("BRONZE_PROVENANCE_REQUIRED")
    if not columns or any(not isinstance(column, str) or not column.strip() for column in columns):
        raise ValueError("INVALID_SOURCE_COLUMNS")
    if len(columns) != len(set(columns)):
        raise ValueError("DUPLICATE_SOURCE_COLUMNS")
    if any(len(row) != len(columns) for row in rows):
        raise ValueError("SOURCE_ROW_WIDTH_MISMATCH")
    if (
        len(locators) != len(rows)
        or any(not isinstance(locator, str) or not locator.strip() for locator in locators)
        or len(set(locators)) != len(locators)
    ):
        raise ValueError("INVALID_SOURCE_LOCATORS")

    missing_columns = set(profile.required_source_columns) - set(columns)
    missing_columns.update(
        rule.field
        for rule in profile.rules
        if rule.field not in columns and rule.required_in_source
    )
    if missing_columns:
        raise ValueError("REQUIRED_SOURCE_FIELD_MISSING:" + ",".join(sorted(missing_columns)))

    field_positions = {column: index for index, column in enumerate(columns)}
    accepted: list[CleanedRecord] = []
    quarantined: list[QuarantinedRecord] = []
    issues: list[MissingValueIssue] = []
    missing_counts: Counter[str] = Counter()

    for raw_row, locator in zip(rows, locators, strict=True):
        original = dict(zip(columns, raw_row, strict=True))
        cleaned = dict(original)
        row_issues: list[MissingValueIssue] = []
        row_quarantined = False

        for rule in profile.rules:
            if rule.field not in field_positions:
                continue
            value = original[rule.field]
            if not _is_missing(value, rule):
                continue

            missing_counts[rule.field] += 1
            if rule.nullable:
                cleaned[rule.field] = None
                row_issues.append(
                    MissingValueIssue(
                        rule_id=rule.rule_id,
                        rule_version=profile.version,
                        domain=profile.domain,
                        entity=profile.entity,
                        field=rule.field,
                        code="NULL_VALUE_PRESERVED",
                        severity="INFO",
                        disposition="PRESERVED",
                        locator=locator,
                    )
                )
            else:
                row_quarantined = True
                row_issues.append(
                    MissingValueIssue(
                        rule_id="CLN-NULL-03",
                        rule_version=profile.version,
                        domain=profile.domain,
                        entity=profile.entity,
                        field=rule.field,
                        code="REQUIRED_VALUE_MISSING",
                        severity="ERROR",
                        disposition="QUARANTINED",
                        locator=locator,
                    )
                )

        issues.extend(row_issues)
        if row_quarantined:
            quarantined.append(QuarantinedRecord(locator, original))
        else:
            accepted.append(CleanedRecord(locator, cleaned))

    return MissingValueResult(
        domain=profile.domain,
        entity=profile.entity,
        batch_id=batch_id,
        records_read=len(rows),
        passed=tuple(accepted),
        quarantined=tuple(quarantined),
        issues=tuple(issues),
        missing_values_by_field=dict(missing_counts),
        provenance=provenance,
    )


def _validate_context(
    request: ReadRequest,
    profile: MissingValueProfile,
    *,
    source_system_id: str,
    data_source_id: str,
    dataset_id: str,
    batch_id: str,
    expected_sha256: str,
    expected_rows: int,
) -> None:
    _validate_profile(profile)
    if any(
        not isinstance(value, str) or not value.strip()
        for value in (source_system_id, data_source_id, dataset_id, batch_id)
    ):
        raise ValueError("INVALID_BRONZE_CONTEXT")
    if not isinstance(request, ReadRequest):
        raise ValueError("INVALID_BRONZE_READ_REQUEST")
    if not isinstance(request.source_type, str) or not request.source_type.strip():
        raise ValueError("UNSUPPORTED_BRONZE_FILE_TYPE")
    if profile.domain != profile.domain.strip() or profile.entity != profile.entity.strip():
        raise ValueError("INVALID_MISSING_VALUE_PROFILE")
    if request.source_type.strip().upper() not in {"CSV", "EXCEL"}:
        raise ValueError("UNSUPPORTED_BRONZE_FILE_TYPE")
    if not isinstance(request.location, str):
        raise ValueError("BRONZE_OBJECT_URI_REQUIRED")
    parsed = urlsplit(request.location)
    if parsed.scheme.lower() != "s3" or not parsed.netloc or not parsed.path.strip("/"):
        raise ValueError("BRONZE_OBJECT_URI_REQUIRED")
    suffix = parsed.path.lower()
    if request.source_type.strip().upper() == "CSV" and not suffix.endswith(".csv"):
        raise ValueError("BRONZE_FILE_TYPE_MISMATCH")
    if request.source_type.strip().upper() == "EXCEL" and not suffix.endswith(".xlsx"):
        raise ValueError("BRONZE_FILE_TYPE_MISMATCH")
    if not request.source_table or request.source_table != parsed.path.rsplit("/", 1)[-1]:
        raise ValueError("BRONZE_SOURCE_TABLE_MISMATCH")
    if (
        not isinstance(request.object_version_id, str)
        or not request.object_version_id.strip()
        or request.object_version_id == "null"
    ):
        raise ValueError("BRONZE_OBJECT_VERSION_REQUIRED")
    if (
        not isinstance(expected_sha256, str)
        or re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256) is None
    ):
        raise ValueError("BRONZE_OBJECT_CHECKSUM_REQUIRED")
    if isinstance(expected_rows, bool) or not isinstance(expected_rows, int) or expected_rows < 0:
        raise ValueError("BRONZE_OBJECT_ROW_COUNT_REQUIRED")


def _validate_profile(profile: MissingValueProfile) -> None:
    if not isinstance(profile, MissingValueProfile):
        raise ValueError("MISSING_VALUE_PROFILE_REQUIRED")
    if any(
        not isinstance(value, str) or not value.strip()
        for value in (profile.domain, profile.entity, profile.version)
    ):
        raise ValueError("INVALID_MISSING_VALUE_PROFILE")
    if not profile.rules:
        raise ValueError("MISSING_VALUE_RULES_REQUIRED")
    if any(not isinstance(rule, MissingValueRule) for rule in profile.rules):
        raise ValueError("INVALID_MISSING_VALUE_RULE")
    fields = [rule.field for rule in profile.rules]
    if len(fields) != len(set(fields)):
        raise ValueError("DUPLICATE_MISSING_VALUE_RULE")
    for rule in profile.rules:
        if (
            not isinstance(rule.field, str)
            or not rule.field.strip()
            or not isinstance(rule.nullable, bool)
            or not isinstance(rule.required_in_source, bool)
            or not isinstance(rule.trim_for_token_match, bool)
            or not isinstance(rule.rule_id, str)
            or not rule.rule_id.strip()
            or not isinstance(rule.null_tokens, tuple)
            or any(not isinstance(token, str) for token in rule.null_tokens)
        ):
            raise ValueError("INVALID_MISSING_VALUE_RULE")
    if not isinstance(profile.required_source_columns, tuple):
        raise ValueError("INVALID_REQUIRED_SOURCE_COLUMNS")
    if any(
        not isinstance(column, str) or not column.strip()
        for column in profile.required_source_columns
    ):
        raise ValueError("INVALID_REQUIRED_SOURCE_COLUMNS")


def _is_missing(value: object, rule: MissingValueRule) -> bool:
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    candidate = value.strip() if rule.trim_for_token_match else value
    return candidate in rule.null_tokens
