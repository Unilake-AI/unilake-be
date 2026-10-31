"""Bàn giao tường minh trong bộ nhớ; bộ điều hợp này không lưu bền vững dữ liệu Bronze."""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING

from app.modules.ingestion.contracts import BronzeBatch, MappingBinding
from app.modules.ingestion.readers.file_snapshot import fingerprint

if TYPE_CHECKING:
    from app.modules.ingestion.readers.contract import ReadResult


def reader_to_batch(
    result: ReadResult,
    *,
    batch_id: str,
    domain_id: str,
    source_type: str,
    binding: MappingBinding,
    input_version: str | None = None,
) -> BronzeBatch:
    """Yêu cầu bằng chứng từ reader; phiên bản kỳ vọng, nếu có, phải khớp bằng chứng."""
    if any(not isinstance(v, str) or not v.strip() for v in (batch_id, domain_id, source_type)):
        raise ValueError("INVALID_BATCH_CONTEXT")
    if not isinstance(binding, MappingBinding) or any(
        not isinstance(v, str) or not v.strip() for v in vars(binding).values()
    ):
        raise ValueError("INVALID_MAPPING_BINDING")
    columns = result.columns
    if not columns or any(not isinstance(c, str) or not c.strip() for c in columns):
        raise ValueError("INVALID_READER_COLUMNS")
    if len(set(columns)) != len(columns):
        raise ValueError("DUPLICATE_READER_COLUMNS")
    if any(len(row) != len(columns) for row in result.rows):
        raise ValueError("READER_ROW_WIDTH_MISMATCH")
    provenance = result.provenance
    if provenance is None:
        raise ValueError("READER_PROVENANCE_REQUIRED")
    if (
        provenance.source_system_id != binding.source_system_id
        or provenance.source_type != source_type
    ):
        raise ValueError("SOURCE_BINDING_MISMATCH")
    if provenance.columns_fingerprint != fingerprint(columns):
        raise ValueError("READER_COLUMNS_CHANGED")
    if input_version is not None and input_version != provenance.input_version:
        raise ValueError("INPUT_VERSION_MISMATCH")
    locators = result.record_locators
    if (
        len(locators) != len(result.rows)
        or any(not isinstance(locator, str) or not locator.strip() for locator in locators)
        or len(set(locators)) != len(locators)
    ):
        raise ValueError("INVALID_READER_LOCATORS")
    return BronzeBatch(
        batch_id=batch_id,
        domain_id=domain_id,
        source_type=source_type,
        records=tuple(deepcopy(dict(zip(columns, row, strict=True))) for row in result.rows),
        metadata={"input_version": provenance.input_version},
        mapping_binding=binding,
        record_locators=locators,
        source_paths=tuple("/" + c.replace("~", "~0").replace("/", "~1") for c in columns),
        provenance=provenance,
    )
