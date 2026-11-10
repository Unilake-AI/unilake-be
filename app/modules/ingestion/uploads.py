"""Tiếp nhận file của người dùng và đưa vào hàng đợi sau khi đã lưu bền vững."""

from __future__ import annotations

import codecs
import hashlib
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import settings
from app.core.exceptions import AppException
from app.modules.ingestion.execution import run_io, upload_domains
from app.providers import upload_writer_client
from app.repositories.ingestion_repo import UPLOAD_JOB_TYPE, IngestionRepository
from app.repositories.upload_repo import upload_target

if TYPE_CHECKING:
    from fastapi import UploadFile
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.modules.ingestion.models import IngestionJob


class UploadParser(BaseModel):
    """Nhận lựa chọn cách đọc file; không nhận đường dẫn hoặc thông tin kết nối."""

    model_config = ConfigDict(extra="forbid")
    delimiter: str = Field(default=",", min_length=1, max_length=1)
    encoding: str = Field(default="utf-8", min_length=1, max_length=100)
    quote_char: str = Field(default='"', min_length=1, max_length=1)
    sheet_name: str | None = Field(default=None, min_length=1, max_length=200)
    header_row: int = Field(default=1, ge=1)
    skip_rows: int = Field(default=0, ge=0)

    def options(self, source_type: str) -> dict[str, Any]:
        if source_type == "CSV":
            codecs.lookup(self.encoding)
            if self.delimiter in {"\r", "\n", "\x00", self.quote_char} or self.quote_char in {
                "\r",
                "\n",
                "\x00",
            }:
                raise ValueError("INVALID_CSV_OPTIONS")
            return {
                "delimiter": self.delimiter,
                "encoding": self.encoding,
                "quote_char": self.quote_char,
                "has_header": True,
                "skip_rows": self.skip_rows,
            }
        return {
            "sheet_name": self.sheet_name,
            "header_row": self.header_row,
            "skip_rows": self.skip_rows,
        }


def parse_options(raw: str, source_type: str) -> dict[str, Any]:
    try:
        return UploadParser.model_validate_json(raw).options(source_type)
    except (ValueError, LookupError, ValidationError):
        raise AppException(
            "Cách đọc file không hợp lệ", code="UPLOAD_PARSER_INVALID", status_code=422
        ) from None


def file_type(filename: str | None) -> tuple[str, str]:
    # Giữ tên gốc khi an toàn; không nhận đường dẫn con hoặc tên có thể thoát khỏi thư mục.
    if not filename or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,199}", filename):
        raise AppException(
            "Tên file chỉ được chứa chữ không dấu, số, dấu gạch và dấu chấm",
            code="UPLOAD_FILENAME_INVALID",
            status_code=422,
        )
    extension = Path(filename).suffix.lower()
    if extension not in {".csv", ".xlsx", ".xlsm"}:
        raise AppException(
            "Chỉ hỗ trợ CSV, XLSX và XLSM", code="UPLOAD_FILE_TYPE_UNSUPPORTED", status_code=415
        )
    return filename, "CSV" if extension == ".csv" else "EXCEL"


def stage_file(
    path: Path, *, dataset_id: int, sha256: str, options: dict[str, Any]
) -> dict[str, Any]:
    storage = upload_writer_client()
    bucket = settings.minio_bucket_uploads
    if bucket in {
        settings.minio_bucket_bronze,
        settings.minio_bucket_silver,
        settings.minio_bucket_gold,
    }:
        raise ValueError("UPLOAD_BUCKET_MUST_BE_SEPARATE")
    if storage.get_bucket_versioning(bucket).status != "Enabled":
        raise ValueError("UPLOAD_VERSIONING_REQUIRED")
    key = f"incoming/{dataset_id}/{uuid4().hex}/{path.name}"
    result = storage.fput_object(bucket, key, str(path), metadata={"sha256": sha256})
    if not result.version_id or result.version_id == "null":
        raise ValueError("UPLOAD_VERSION_REQUIRED")
    stat = storage.stat_object(bucket, key, version_id=result.version_id)
    stored_size = stat.size
    if (
        stat.version_id != result.version_id
        or stored_size is None
        or stored_size != path.stat().st_size
    ):
        raise ValueError("UPLOAD_STORAGE_MISMATCH")
    # Đọc lại đúng phiên bản để chỉ tạo job khi nội dung đã được lưu đầy đủ.
    response = storage.get_object(bucket, key, version_id=result.version_id)
    try:
        digest, count, started = hashlib.sha256(), 0, monotonic()
        for chunk in response.stream(1024 * 1024):
            count += len(chunk)
            if count > stored_size or monotonic() - started > settings.upload_timeout_seconds:
                raise ValueError("UPLOAD_STORAGE_LIMIT_EXCEEDED")
            digest.update(chunk)
        if (
            count != stored_size
            or response.headers.get("x-amz-version-id") != result.version_id
            or digest.hexdigest() != sha256
        ):
            raise ValueError("UPLOAD_CHECKSUM_MISMATCH")
    finally:
        response.close()
        response.release_conn()
    return {
        "uri": f"s3://{bucket}/{key}",
        "version_id": result.version_id,
        "sha256": sha256,
        "size_bytes": stored_size,
        "filename": path.name,
        "parser_options": options,
    }


async def receive_upload(
    db: AsyncSession, file: UploadFile, dataset_id: int, parser_options: str, user: dict[str, Any]
) -> IngestionJob:
    allowed_domains = upload_domains(user)
    filename, source_type = file_type(file.filename)
    options = parse_options(parser_options, source_type)
    actor = str(user.get("sub", ""))[:100]
    source, dataset = await upload_target(
        db, dataset_id, source_type, actor, allowed_domains=allowed_domains
    )
    try:
        with TemporaryDirectory(prefix="unilake_upload_") as directory:
            path = Path(directory) / filename
            digest, size = hashlib.sha256(), 0
            # Đọc từng phần để không giữ toàn bộ file trong bộ nhớ; dừng ngay khi vượt giới hạn.
            with path.open("wb") as output:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > settings.upload_max_bytes:
                        raise AppException(
                            "File vượt dung lượng cho phép",
                            code="UPLOAD_TOO_LARGE",
                            status_code=413,
                        )
                    digest.update(chunk)
                    output.write(chunk)
            if size == 0:
                raise AppException("File không có nội dung", code="UPLOAD_EMPTY", status_code=422)
            reference = await run_io(
                stage_file, path, dataset_id=dataset_id, sha256=digest.hexdigest(), options=options
            )
        # Job chỉ nhìn thấy được sau khi file tiếp nhận đã lưu bền vững và có phiên bản cố định.
        reference.update(
            source_type=source_type,
            source_system_id=source.source_system_id,
            data_source_id=source.data_source_id,
            dataset_id=dataset_id,
            domain=dataset.domain,
            limits={
                "max_file_bytes": settings.upload_max_bytes,
                "max_columns": settings.upload_max_columns,
                "max_records": settings.upload_max_records,
                "max_cell_chars": settings.upload_max_cell_chars,
                "timeout_seconds": settings.upload_timeout_seconds,
                "memory_limit_bytes": 256 * 1024 * 1024,
            },
            max_expanded_bytes=settings.upload_max_expanded_bytes,
        )
        return await IngestionRepository(db).create(
            job_type=UPLOAD_JOB_TYPE,
            data_source_id=source.data_source_id,
            dataset_id=dataset_id,
            target_layer_id=dataset.data_layer_id,
            source_table=filename,
            requested_by=actor,
            status="QUEUED",
            stage="QUEUED",
            upload_input=reference,
        )
    finally:
        await file.close()
