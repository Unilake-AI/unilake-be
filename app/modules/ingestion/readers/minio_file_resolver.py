"""Resolve file S3/MinIO thành file local tạm cho ingestion reader."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from time import monotonic
from typing import TYPE_CHECKING
from urllib.parse import SplitResult, urlsplit

from minio import Minio

from app.infra.minio.client import source_reader_client
from app.modules.ingestion.input_validator import InputValidationError

if TYPE_CHECKING:
    from .contract import ReadRequest


FILE_SOURCE_TYPES = {"CSV", "EXCEL"}
S3_SCHEME = "s3"
TEMP_DIR_PREFIX = "ingestion_source_"


@contextmanager
def resolve_file_request(
    request: ReadRequest,
) -> Iterator[ReadRequest]:
    """Tải file S3/MinIO về file tạm để CSV/Excel reader đọc như file local.

    CSVReader và ExcelReader hiện chỉ xử lý file local. Hàm này đứng giữa
    source_loader và reader: nếu location là `s3://bucket/path/file.csv`,
    resolver tải object đó về thư mục tạm, đổi `request.location` sang đường
    dẫn local, rồi tự xóa file tạm sau khi reader đọc xong.
    """

    location = request.location

    # 1. Chỉ CSV/Excel có file cần tải. PostgreSQL hoặc local path thì giữ nguyên request.
    if (
        request.source_type.upper() not in FILE_SOURCE_TYPES
        or not isinstance(location, str)
        or not _is_s3_location(location)
    ):
        yield request
        return

    # 2. Parse `s3://bucket/object/key` thành bucket và object key.
    parsed = urlsplit(location)
    bucket, object_key = _parse_s3_location(parsed)

    filename = PurePosixPath(object_key).name

    if filename in {"", ".", ".."}:
        raise ValueError("Tên file S3 không hợp lệ")

    # 3. source_table phải có và phải khớp tên file trong S3 location.
    # Điều này tránh caller yêu cầu đọc nhầm object khác với metadata DataSource.
    if not request.source_table:
        raise ValueError("Thiếu source_table")

    if request.source_table != filename:
        raise ValueError("source_table không khớp với tên file trong S3 location")

    # 4. Chọn SA chỉ đọc đúng kho: Bronze reader hoặc SA đọc hệ thống nguồn.
    client = _create_minio_client(bucket)

    # Cố định phiên bản cụ thể khi bucket hỗ trợ quản lý phiên bản. "null" có thể
    # bị thay thế trong bucket không bật hoặc đã tạm ngưng quản lý phiên bản nên không bảo đảm đọc lại.
    version_id = request.object_version_id
    if version_id is not None and (
        not isinstance(version_id, str) or not version_id.strip() or version_id == "null"
    ):
        raise ValueError("INVALID_OBJECT_VERSION")
    if version_id is None:
        current = client.stat_object(bucket_name=bucket, object_name=object_key)
        candidate = current.version_id
        version_id = (
            candidate if isinstance(candidate, str) and candidate and candidate != "null" else None
        )

    # 5. File tạm chỉ sống trong phạm vi context manager này.
    # Khi reader đọc xong hoặc ném lỗi, TemporaryDirectory vẫn được dọn.
    with TemporaryDirectory(prefix=TEMP_DIR_PREFIX) as temp_dir:
        local_path = Path(temp_dir) / filename

        if request.validation_policy is not None:
            limits = request.validation_policy.limits
            # Validation path tải có giới hạn, không fget toàn object rồi mới chặn.
            stat = client.stat_object(bucket, object_key, version_id=version_id)
            if stat.size is None:
                raise InputValidationError("IN-COM-003", "SOURCE_UNAVAILABLE", infrastructure=True)
            if stat.size > limits.max_file_bytes:
                raise InputValidationError("IN-COM-005", "RESOURCE_LIMIT_EXCEEDED")
            response = client.get_object(bucket, object_key, version_id=version_id)
            started, total = monotonic(), 0
            try:
                with local_path.open("wb") as stream:
                    for chunk in response.stream(1024 * 1024):
                        total += len(chunk)
                        if (
                            total > limits.max_file_bytes
                            or monotonic() - started > limits.timeout_seconds
                        ):
                            raise InputValidationError("IN-COM-005", "RESOURCE_LIMIT_EXCEEDED")
                        stream.write(chunk)
                if total != stat.size:
                    raise InputValidationError("IN-COM-008", "SOURCE_CHANGED")
                actual_version = response.headers.get("x-amz-version-id")
                if version_id is not None and actual_version != version_id:
                    raise InputValidationError("IN-COM-008", "SOURCE_CHANGED")
            finally:
                response.close()
                response.release_conn()
            yield replace(request, location=str(local_path), object_version_id=version_id)
            return

        downloaded = _download_object(
            client=client,
            bucket=bucket,
            object_key=object_key,
            local_path=local_path,
            version_id=version_id,
        )
        actual_version = getattr(downloaded, "version_id", None)
        if isinstance(actual_version, str) and actual_version != "null":
            if version_id is not None and actual_version != version_id:
                raise ValueError("OBJECT_VERSION_MISMATCH")
            version_id = actual_version

        local_request = replace(
            request,
            location=str(local_path),
            object_version_id=version_id,
        )

        yield local_request


def _is_s3_location(location: str) -> bool:
    """Kiểm tra location có phải URI S3/MinIO không."""

    return urlsplit(location).scheme.lower() == S3_SCHEME


def _parse_s3_location(parsed_url: SplitResult) -> tuple[str, str]:
    """Validate và tách S3 URI thành bucket, object_key."""

    bucket = parsed_url.netloc
    object_key = parsed_url.path.lstrip("/")

    if not bucket or not object_key or parsed_url.query or parsed_url.fragment:
        raise ValueError("Đường dẫn S3 không hợp lệ")

    return bucket, object_key


def _create_minio_client(bucket: str) -> Minio:
    """Runtime không dùng credentials quản trị khi tải object nguồn."""
    return source_reader_client(bucket)


def _download_object(
    *,
    client: Minio,
    bucket: str,
    object_key: str,
    local_path: Path,
    version_id: str | None = None,
) -> object:
    """Tải một object từ MinIO xuống đường dẫn local tạm."""

    if version_id is not None:
        return client.fget_object(
            bucket_name=bucket,
            object_name=object_key,
            file_path=str(local_path),
            version_id=version_id,
        )
    return client.fget_object(
        bucket_name=bucket,
        object_name=object_key,
        file_path=str(local_path),
    )
