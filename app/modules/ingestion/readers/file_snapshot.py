"""Chụp byte một lần, tính dấu vân tay và phân tích bản sao riêng đó trong worker."""

from __future__ import annotations

import hashlib
import json
import shutil
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from app.modules.ingestion.contracts import FileProvenance

from .minio_file_resolver import resolve_file_request

if TYPE_CHECKING:
    from .contract import ReadRequest, ReadResult, SourceReader


def fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def read_file_snapshot(
    request: ReadRequest,
    reader: SourceReader,
    *,
    source_system_id: str,
    data_source_id: str,
    dataset_id: str,
) -> ReadResult:
    """Không ngụ ý lưu bền vững hay chụp cơ sở dữ liệu. Chỉ hỗ trợ CSV/EXCEL."""
    source_type = request.source_type.strip().upper()
    if source_type not in {"CSV", "EXCEL"}:
        raise ValueError("FILE_SNAPSHOT_REQUIRED")
    if any(
        not isinstance(v, str) or not v.strip()
        for v in (source_system_id, data_source_id, dataset_id)
    ):
        raise ValueError("SOURCE_CONTEXT_REQUIRED")
    if not request.location:
        raise ValueError("SOURCE_LOCATION_REQUIRED")
    original_ref = request.location
    request = replace(
        request, source_type=source_type, configuration=deepcopy(request.configuration)
    )
    # Cùng file nhưng khác cấu hình/phiên bản parser có thể cho kết quả đọc khác nhau.
    # Lưu dấu vân tay này trong provenance để truy vết cách dữ liệu được diễn giải.
    parser_hash = fingerprint(
        {
            "reader_contract": "file-v1",
            "source_type": source_type,
            "configuration": request.configuration,
            "parser_version": version("openpyxl" if source_type == "EXCEL" else "duckdb"),
        }
    )
    with (
        resolve_file_request(request) as resolved,
        TemporaryDirectory(prefix="normalization_snapshot_") as directory,
    ):
        path = Path(resolved.location or "").expanduser()
        if path.is_dir():
            if (
                not resolved.source_table
                or Path(resolved.source_table).name != resolved.source_table
            ):
                raise ValueError("INVALID_SOURCE_TABLE")
            path = path / resolved.source_table
        if resolved.source_table and path.name != resolved.source_table:
            raise ValueError("SOURCE_TABLE_MISMATCH")
        if not original_ref.lower().startswith("s3://"):
            if request.object_version_id is not None:
                raise ValueError("OBJECT_VERSION_REQUIRES_S3")
            original_ref = str(path.resolve())
        snapshot = Path(directory) / path.name
        # Băm chính bản sao sẽ được parser đọc, không mở lại nguồn để tính checksum.
        with path.open("rb") as source, snapshot.open("wb") as target:
            shutil.copyfileobj(source, target)
        with snapshot.open("rb") as stream:
            checksum = hashlib.file_digest(stream, "sha256").hexdigest()
        result = reader.read(replace(resolved, location=str(snapshot)))
        with snapshot.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != checksum:
                raise ValueError("SNAPSHOT_CHANGED_DURING_READ")
        # Excel giữ tọa độ sheet/dòng thực tế. CSV dùng thứ tự bản ghi đã parse,
        # vì một bản ghi CSV có thể chứa xuống dòng và chiếm nhiều dòng vật lý.
        if source_type == "EXCEL":
            if len(result.record_locators) != len(result.rows):
                raise ValueError("EXCEL_LOCATORS_REQUIRED")
            locators = result.record_locators
        else:
            locators = tuple(f"record:{i}" for i in range(1, len(result.rows) + 1))
        provenance = FileProvenance(
            source_system_id,
            data_source_id,
            dataset_id,
            source_type,
            original_ref,
            resolved.object_version_id,
            checksum,
            parser_hash,
            fingerprint(result.columns),
            datetime.now(UTC).isoformat(),
            "excel_sheet_row" if source_type == "EXCEL" else "parsed_record_ordinal",
            result.workbook_epoch,
        )
        return replace(result, record_locators=locators, provenance=provenance)
