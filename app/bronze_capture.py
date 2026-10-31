"""Bộ điều hợp tích hợp: tạo bản sao tệp bất biến và lưu bền vững vào MinIO."""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import replace
from datetime import UTC
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq

from app.core.config import settings
from app.infra.minio.bronze import object_key
from app.infra.minio.client import bronze_verifier_client, bronze_writer_client
from app.modules.ingestion.contracts import CapturedArtifact
from app.modules.ingestion.input_validator import (
    InputValidationError,
    InputValidator,
    ValidationBlockedError,
)
from app.modules.ingestion.readers.factory import SourceReaderFactory
from app.modules.ingestion.readers.minio_file_resolver import resolve_file_request

if TYPE_CHECKING:
    from types import TracebackType

    from app.modules.ingestion.contracts import BronzeWriteContext
    from app.modules.ingestion.readers.contract import ReadRequest


class BronzeCapture:
    def __enter__(self) -> BronzeCapture:
        self.temp = TemporaryDirectory(prefix="bronze_execution_")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.temp.cleanup()

    def read(self, request: ReadRequest) -> CapturedArtifact:
        if request.validation_policy is not None:
            return self.read_validated(request)
        reader = SourceReaderFactory.get_reader(request.source_type)
        version = None
        if request.source_type in {"CSV", "EXCEL"}:
            with resolve_file_request(request) as resolved:
                if not resolved.location or not request.source_table:
                    raise ValueError("SOURCE_LOCATION_AND_TABLE_REQUIRED")
                origin = Path(resolved.location).expanduser()
                if origin.is_dir():
                    origin = origin / request.source_table
                if origin.name != request.source_table:
                    raise ValueError("SOURCE_TABLE_MISMATCH")
                path = Path(self.temp.name) / origin.name
                # Đọc và lưu Bronze từ cùng một bản sao, tránh nguồn bị sửa giữa hai bước.
                shutil.copyfile(origin, path)
                version = resolved.object_version_id
            result = reader.read(replace(request, location=str(path)))
        else:
            result = reader.read(request)
            path = Path(self.temp.name) / "snapshot.parquet"
            # Xuất DB sang Parquet để giữ kiểu số/ngày; đổi sang chuỗi sẽ mất thông tin kiểu.
            table = pa.table(
                {name: [row[i] for row in result.rows] for i, name in enumerate(result.columns)}
            )
            pq.write_table(table, path)
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        return CapturedArtifact(path, result.rows_read, digest, version)

    def read_validated(self, request: ReadRequest) -> CapturedArtifact:
        if request.validation_policy is None or request.ingestion_job_id is None:
            raise ValueError("VALIDATION_CONTEXT_REQUIRED")
        validator = InputValidator(request.validation_policy, request.ingestion_job_id)
        version = None
        try:
            if request.source_type != request.validation_policy.parser.kind:
                raise InputValidationError("IN-COM-002", "SOURCE_SCHEMA_MISMATCH")
            if request.source_type in {"CSV", "EXCEL"}:
                with resolve_file_request(request) as resolved:
                    origin = Path(resolved.location or "").expanduser()
                    if origin.is_dir():
                        origin /= request.source_table or ""
                    if origin.name != request.source_table:
                        raise InputValidationError("IN-COM-002", "SOURCE_SCHEMA_MISMATCH")
                    before = origin.stat()
                    validator.budget(size=before.st_size)
                    path = Path(self.temp.name) / origin.name
                    # Copy có giới hạn; nguồn tăng kích thước trong lúc copy cũng bị chặn.
                    copied = 0
                    with origin.open("rb") as source, path.open("wb") as target:
                        while chunk := source.read(1024 * 1024):
                            copied += len(chunk)
                            validator.budget(size=copied)
                            target.write(chunk)
                    after = origin.stat()
                    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                        raise InputValidationError("IN-COM-008", "SOURCE_CHANGED")
                    version = resolved.object_version_id
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                validator.file(path)
                with path.open("rb") as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                        raise InputValidationError("IN-COM-008", "SOURCE_CHANGED")
                validator.pass_rules("IN-COM-008")
            else:
                path = Path(self.temp.name) / "snapshot.parquet"
                validator.postgres(request, path)
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
            validator.budget()
        except Exception as exc:
            raise ValidationBlockedError(validator.failure_report(exc)) from None
        report = validator.report()
        if not report.can_accept:
            raise ValidationBlockedError(report)
        return CapturedArtifact(path, validator.rows, digest, version, report)


def store_bronze(
    artifact: CapturedArtifact,
    domain: str,
    job: BronzeWriteContext,
) -> dict[str, Any]:
    """Ghi raw file và metadata UDL-141, xác minh đúng phiên bản trước khi công bố."""
    # Hai client khác nhau: writer không thể đọc object, verifier không thể ghi/xóa.
    storage = bronze_writer_client()
    verifier = bronze_verifier_client()
    bucket = settings.minio_bucket_bronze
    # URI chưa đủ để đọc lại đúng dữ liệu: cần version_id của lần ghi cụ thể.
    if storage.get_bucket_versioning(bucket).status != "Enabled":
        raise RuntimeError("BRONZE_VERSIONING_REQUIRED")
    created_at = job.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    day = created_at.astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).date()
    # DB hiện lưu UTC không timezone; datetime có offset vẫn phải giữ đúng thời điểm.
    captured_at = job.ingestion_timestamp
    if captured_at.tzinfo is None:
        captured_at = captured_at.replace(tzinfo=UTC)
    batch_id = f"job-{job.ingestion_job_id}"
    metadata = {
        "sha256": artifact.sha256,
        "source-system-id": str(job.source_system_id),
        "ingestion-timestamp": captured_at.astimezone(UTC).isoformat(),
        "batch-id": batch_id,
    }
    key = object_key(domain, day, batch_id, artifact.path.name)
    # MinIO lưu các trường này dưới dạng HTTP header x-amz-meta-*, không sửa raw bytes.
    uploaded = storage.fput_object(bucket, key, str(artifact.path), metadata=dict(metadata))
    if not uploaded.version_id or uploaded.version_id == "null":
        raise RuntimeError("BRONZE_VERSION_REQUIRED")
    # Pin version để tránh kiểm tra nhầm object nếu cùng key vừa được ghi phiên bản mới.
    stat = verifier.stat_object(bucket, key, version_id=uploaded.version_id)
    stored_metadata = {name.lower(): value for name, value in (stat.metadata or {}).items()}
    if any(stored_metadata.get(f"x-amz-meta-{name}") != value for name, value in metadata.items()):
        raise RuntimeError("BRONZE_METADATA_MISMATCH")
    # Đọc lại đúng phiên bản vừa ghi và kiểm tra nội dung trước khi báo lưu thành công.
    response = verifier.get_object(bucket, key, version_id=uploaded.version_id)
    try:
        digest = hashlib.sha256()
        for chunk in response.stream(1024 * 1024):
            digest.update(chunk)
        if digest.hexdigest() != artifact.sha256:
            raise RuntimeError("BRONZE_CHECKSUM_MISMATCH")
    finally:
        response.close()
        response.release_conn()
    return {
        "uri": f"s3://{bucket}/{key}",
        "version_id": uploaded.version_id,
        "sha256": artifact.sha256,
        "size_bytes": artifact.path.stat().st_size,
        "source_version_id": artifact.source_version_id,
        # Giữ cùng metadata trong kết quả job để đối chiếu object với lịch sử ingestion.
        "metadata": metadata,
    }
