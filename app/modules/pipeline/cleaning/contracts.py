"""Làm sạch dữ liệu Bronze trước khi chuẩn hóa theo bộ quy tắc đã chọn."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol
from urllib.parse import urlsplit

from app.modules.pipeline.cleaning.duplicate_records import (
    DuplicateIssue,
    DuplicateProfile,
)
from app.modules.pipeline.cleaning.format_validation import (
    FormatIssue,
    FormatProfile,
)
from app.modules.pipeline.cleaning.missing_values import (
    MissingValueIssue,
    MissingValueProfile,
)
from app.shared.fingerprints import fingerprint

if TYPE_CHECKING:
    from app.modules.ingestion.contracts import BronzeBatch


CleaningIssue = MissingValueIssue | FormatIssue | DuplicateIssue


@dataclass(frozen=True)
class CleaningTask:
    """Dữ liệu Bronze và các quy tắc làm sạch phù hợp với nguồn dữ liệu."""

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
    quarantined_locators: tuple[str, ...] = ()


class CleaningReportSink(Protocol):
    """Lưu kết quả làm sạch và các vấn đề phát hiện được để tra cứu sau này."""

    def publish(self, report: CleaningReport) -> None: ...


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
