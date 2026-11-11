"""Lưu hồ sơ dòng lỗi và đọc lại đúng bản ghi nguồn đã ghim."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit
from uuid import uuid4
from zipfile import ZipFile

from sqlalchemy import insert

from app.core.config import settings
from app.modules.ingestion.contracts.readers import (
    ReadRequest,
    SourceReaderFactory,
    read_file_snapshot,
)
from app.modules.pipeline.models import QUARANTINE_RECORDS
from app.providers import bronze_reader_client

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

    from app.modules.ingestion.contracts import BronzeBatch
    from app.modules.ingestion.contracts.readers import ReadResult
    from app.modules.pipeline.cleaning_pipeline import CleaningResult

MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_EXCEL_EXPANDED_BYTES = 32 * 1024 * 1024
SOURCE_READ_SECONDS = 15

_PARSER_KEYS = {
    "CSV": ("delimiter", "encoding", "has_header", "quote_char", "skip_rows"),
    "EXCEL": ("sheet_name", "header_row", "skip_rows"),
}


def file_parser_options(source_type: str, configuration: dict[str, Any]) -> dict[str, Any]:
    # Chỉ giữ tùy chọn reader thực sự sử dụng, bỏ các thiết lập ngoài parser và thông tin kết nối.
    return {key: configuration[key] for key in _PARSER_KEYS[source_type] if key in configuration}


def quarantine_source(context: dict[str, Any], batch: BronzeBatch) -> dict[str, Any]:
    if batch.provenance is None:
        raise ValueError("BRONZE_PROVENANCE_REQUIRED")
    output = context["output"]
    return {
        "uri": output["uri"],
        "version_id": output["version_id"],
        "sha256": output["sha256"],
        "size_bytes": output["size_bytes"],
        "source_type": context["source_type"],
        "file_name": context["source_table"],
        "parser_options": file_parser_options(context["source_type"], context["configuration"]),
        "parser_fingerprint": batch.provenance.parser_fingerprint,
        "columns_fingerprint": batch.provenance.columns_fingerprint,
        "source_system_id": batch.provenance.source_system_id,
        "data_source_id": batch.provenance.data_source_id,
        "dataset_id": batch.provenance.dataset_id,
    }


def save_quarantine_records(
    conn: Connection, run_id: str, original: BronzeBatch, cleaned: CleaningResult
) -> None:
    # Một hồ sơ cho một dòng, dù bước kiểm tra phát hiện nhiều lỗi trên dòng đó.
    by_locator: dict[str, list[dict[str, Any]]] = defaultdict(list)
    quarantined = set(cleaned.quarantined_locators)
    for issue in cleaned.report.issues:
        if issue.locator in quarantined and issue.disposition == "QUARANTINED":
            by_locator[issue.locator].append(asdict(issue))
    if set(by_locator) != quarantined or len(quarantined) != cleaned.report.records_quarantined:
        raise ValueError("QUARANTINE_RECORDS_INCOMPLETE")
    conn.execute(
        insert(QUARANTINE_RECORDS),
        [
            {
                "record_id": str(uuid4()),
                "cleaning_run_id": run_id,
                "locator": locator,
                "record_position": position,
                "issues": by_locator[locator],
                "error_codes": sorted({issue["code"] for issue in by_locator[locator]}),
            }
            for position, locator in enumerate(original.record_locators, start=1)
            if locator in quarantined
        ],
    )


class SourceReadLimitError(ValueError):
    """Vượt giới hạn đọc dòng nguồn tương tác."""


def read_bronze_snapshot(
    source: dict[str, Any], *, require_provenance_fingerprints: bool = True
) -> ReadResult:
    """Đọc lại đúng phiên bản file Bronze theo bằng chứng provenance đã lưu."""
    uri = urlsplit(str(source.get("uri", "")))
    filename = source.get("file_name")
    version_id = source.get("version_id")
    sha256 = source.get("sha256")
    size_bytes = source.get("size_bytes")
    source_type = source.get("source_type")
    parser_options = source.get("parser_options")
    key = uri.path.lstrip("/")
    if (
        not isinstance(filename, str)
        or not filename
        or uri.scheme != "s3"
        or uri.netloc != settings.minio_bucket_bronze
        or uri.query
        or uri.fragment
        or not key
        or Path(filename).name != filename
        or Path(key).name != filename
        or not isinstance(version_id, str)
        or not version_id
        or version_id == "null"
        or not isinstance(sha256, str)
        or type(size_bytes) is not int
        or size_bytes < 0
        or source_type not in {"CSV", "EXCEL"}
        or not isinstance(parser_options, dict)
    ):
        raise ValueError("INVALID_QUARANTINE_SOURCE")
    if size_bytes > MAX_SOURCE_BYTES:
        raise SourceReadLimitError("QUARANTINE_SOURCE_TOO_LARGE")
    deadline = monotonic() + SOURCE_READ_SECONDS

    def check_deadline() -> None:
        if monotonic() >= deadline:
            raise TimeoutError("QUARANTINE_SOURCE_TIMEOUT")

    with TemporaryDirectory(prefix="quarantine_source_") as directory:
        path = Path(directory) / filename
        client = bronze_reader_client(timeout=SOURCE_READ_SECONDS)
        response = client.get_object(uri.netloc, key, version_id=version_id)
        try:
            if response.headers.get("x-amz-version-id") != version_id:
                raise ValueError("QUARANTINE_SOURCE_VERSION_MISMATCH")
            digest = hashlib.sha256()
            size = 0
            with path.open("wb") as target:
                for chunk in response.stream(64 * 1024):
                    check_deadline()
                    size += len(chunk)
                    if size > MAX_SOURCE_BYTES:
                        raise SourceReadLimitError("QUARANTINE_SOURCE_TOO_LARGE")
                    digest.update(chunk)
                    target.write(chunk)
        finally:
            response.close()
            response.release_conn()
        if size != size_bytes or digest.hexdigest() != sha256.lower():
            raise ValueError("QUARANTINE_SOURCE_CHECKSUM_MISMATCH")
        if source_type == "EXCEL":
            # Giới hạn kích thước sau giải nén vì XLSX là một gói ZIP.
            with ZipFile(path) as workbook:
                if sum(item.file_size for item in workbook.infolist()) > MAX_EXCEL_EXPANDED_BYTES:
                    raise SourceReadLimitError("QUARANTINE_SOURCE_TOO_LARGE")
        check_deadline()
        parsed = read_file_snapshot(
            ReadRequest(source_type, str(path), parser_options, filename),
            SourceReaderFactory.get_reader(source_type),
            source_system_id=str(source["source_system_id"]),
            data_source_id=str(source["data_source_id"]),
            dataset_id=str(source["dataset_id"]),
        )
        check_deadline()
        provenance = parsed.provenance
        if provenance is None:
            raise ValueError("QUARANTINE_SOURCE_PROVENANCE_MISSING")
        if (require_provenance_fingerprints or "parser_fingerprint" in source) and (
            provenance.parser_fingerprint != source.get("parser_fingerprint")
        ):
            raise ValueError("QUARANTINE_SOURCE_LOCATOR_MISMATCH")
        if (require_provenance_fingerprints or "columns_fingerprint" in source) and (
            provenance.columns_fingerprint != source.get("columns_fingerprint")
        ):
            raise ValueError("QUARANTINE_SOURCE_LOCATOR_MISMATCH")
        return parsed


def read_quarantine_source(record: dict[str, Any]) -> dict[str, Any]:
    source = record["quarantine_source"]
    parsed = read_bronze_snapshot(source, require_provenance_fingerprints=True)
    position = record["record_position"]
    if (
        not 1 <= position <= parsed.rows_read
        or parsed.record_locators[position - 1] != record["locator"]
    ):
        raise ValueError("QUARANTINE_SOURCE_LOCATOR_MISMATCH")
    return dict(zip(parsed.columns, parsed.rows[position - 1], strict=True))
