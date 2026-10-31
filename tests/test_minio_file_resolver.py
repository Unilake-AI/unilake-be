from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.modules.ingestion.readers.contract import ReadRequest
from app.modules.ingestion.readers.minio_file_resolver import resolve_file_request


def _csv_request(location: str = "s3://bronze/admissions/applicant.csv") -> ReadRequest:
    """Tạo request CSV trỏ tới MinIO để test resolver."""

    return ReadRequest(
        source_type="CSV",
        location=location,
        configuration={"delimiter": ",", "encoding": "utf-8", "has_header": True},
        source_table="applicant.csv",
    )


def test_resolve_file_request_downloads_s3_object_to_temp_file() -> None:
    """Resolver tải object S3 về file tạm và đổi request.location sang path local."""

    client = MagicMock()

    with (
        patch(
            "app.modules.ingestion.readers.minio_file_resolver._create_minio_client",
            return_value=client,
        ) as minio_mock,
        resolve_file_request(_csv_request()) as resolved_request,
    ):
        assert resolved_request.source_type == "CSV"
        assert resolved_request.source_table == "applicant.csv"
        assert resolved_request.location is not None
        assert Path(resolved_request.location).name == "applicant.csv"
        assert "s3://" not in resolved_request.location

        client.fget_object.assert_called_once_with(
            bucket_name="bronze",
            object_name="admissions/applicant.csv",
            file_path=resolved_request.location,
        )

    minio_mock.assert_called_once()


def test_resolve_file_request_keeps_local_request_unchanged() -> None:
    """Local file request không cần MinIO nên được yield nguyên trạng."""

    request = _csv_request(location="/data/applicant.csv")

    with resolve_file_request(request) as resolved_request:
        assert resolved_request is request


def test_resolve_file_request_keeps_postgresql_request_unchanged() -> None:
    """PostgreSQL không phải file source nên resolver không tải gì từ MinIO."""

    request = ReadRequest(
        source_type="POSTGRESQL",
        location=None,
        configuration={},
        source_table="public.student",
    )

    with resolve_file_request(request) as resolved_request:
        assert resolved_request is request


def test_resolve_file_request_rejects_invalid_s3_location() -> None:
    """S3 URI phải có bucket, object key và không có query/fragment."""

    request = _csv_request(location="s3://bronze/admissions/applicant.csv?version=1")

    with (
        pytest.raises(ValueError, match="Đường dẫn S3 không hợp lệ"),
        resolve_file_request(request),
    ):
        pass


def test_resolve_file_request_rejects_source_table_mismatch() -> None:
    """source_table phải khớp tên file cuối cùng trong S3 location."""

    request = _csv_request()
    request.source_table = "other.csv"

    with pytest.raises(ValueError, match="source_table không khớp"), resolve_file_request(request):
        pass


def test_resolve_file_request_rejects_missing_source_table() -> None:
    """S3 file source bắt buộc có source_table để đối chiếu tên file."""

    request = _csv_request()
    request.source_table = None

    with pytest.raises(ValueError, match="Thiếu source_table"), resolve_file_request(request):
        pass
