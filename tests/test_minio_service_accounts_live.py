"""Kiểm tra quyền thật UDL-142 bằng object giả lập riêng; không đụng raw data có sẵn."""

from __future__ import annotations

import os
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from dotenv import dotenv_values
from minio import Minio
from minio.commonconfig import Tags
from minio.error import S3Error

from app.core.config import settings
from app.infra.minio.bronze import client

pytestmark = pytest.mark.skipif(
    os.getenv("BRONZE_IAM_LIVE_TEST") != "1", reason="Requires provisioned MinIO service accounts"
)


def assert_denied(operation: Callable[[], object]) -> None:
    """Chỉ AccessDenied mới chứng minh IAM chặn; lỗi mạng/không tồn tại không đủ."""
    with pytest.raises(S3Error) as exc:
        operation()
    assert exc.value.code == "AccessDenied"


def test_bronze_write_only_and_read_only_accounts() -> None:
    credentials = dotenv_values(Path(__file__).resolve().parents[1] / ".env.bronze-accounts.local")

    def account(role: str) -> Minio:
        return Minio(
            settings.minio_endpoint,
            access_key=credentials[f"BRONZE_{role}_ACCESS_KEY"],
            secret_key=credentials[f"BRONZE_{role}_SECRET_KEY"],
            region=settings.minio_region,
            secure=settings.minio_secure,
        )

    writer = account("WRITER")
    bucket = settings.minio_bucket_bronze
    key = f"iam-check/{uuid4().hex}/synthetic.txt"
    data = b"UDL-142 permission check\n"
    overwrite = b"UNEXPECTED overwrite must be denied\n"
    admin = client()
    try:
        uploaded = writer.put_object(bucket, key, BytesIO(data), len(data))
        assert uploaded.version_id
        assert_denied(lambda: writer.get_object(bucket, key))
        assert_denied(lambda: writer.get_object(bucket, key, version_id=uploaded.version_id))
        assert_denied(lambda: writer.remove_object(bucket, key))
        assert_denied(lambda: writer.remove_object(bucket, key, version_id=uploaded.version_id))
        for role in ("VERIFIER", "READER"):
            reader = account(role)
            if role == "READER":
                # ETL có thể tìm object/version, không cần mượn quyền admin để list.
                assert any(
                    o.object_name == key
                    for o in reader.list_objects(bucket, prefix=key, recursive=True)
                )
                assert any(
                    o.version_id == uploaded.version_id
                    for o in reader.list_objects(
                        bucket, prefix=key, recursive=True, include_version=True
                    )
                )
            response = reader.get_object(bucket, key, version_id=uploaded.version_id)
            try:
                assert response.read() == data
            finally:
                response.close()
                response.release_conn()
            assert_denied(
                lambda reader=reader: reader.put_object(
                    bucket, key, BytesIO(overwrite), len(overwrite)
                )
            )
            assert_denied(
                lambda reader=reader: reader.remove_object(
                    bucket, key, version_id=uploaded.version_id
                )
            )
            if role == "READER":
                assert_denied(lambda reader=reader: reader.remove_object(bucket, key))
                tags = Tags.new_object_tags()
                tags["changed-by"] = "permission-test"
                assert_denied(
                    lambda reader=reader, tags=tags: reader.set_object_tags(
                        bucket, key, tags, version_id=uploaded.version_id
                    )
                )
        # Sau các ca negative, bản mới nhất vẫn phải là dữ liệu gốc, không có version ghi đè.
        downstream = account("READER")
        assert downstream.stat_object(bucket, key).version_id == uploaded.version_id
        response = downstream.get_object(bucket, key)
        try:
            assert response.read() == data
        finally:
            response.close()
            response.release_conn()
    finally:
        # Chỉ dọn các version thuộc đúng UUID vừa tạo, kể cả test quyền ghi/xóa bị lỗi.
        for obj in admin.list_objects(bucket, prefix=key, include_version=True, recursive=True):
            if obj.object_name == key:
                admin.remove_object(bucket, key, version_id=obj.version_id)
        # Xác nhận Admin đã dọn cả version và delete marker, không chỉ ẩn file hiện tại.
        assert not list(
            admin.list_objects(bucket, prefix=key, include_version=True, recursive=True)
        )
