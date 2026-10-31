"""Kiểm tra MinIO khi chủ động bật; chỉ xóa các phiên bản do kiểm thử này tạo."""

from __future__ import annotations

import os
import sys
from datetime import date
from uuid import uuid4

import pytest

from app.core.config import settings
from app.infra.minio.bronze import client, main, object_key, setup_minio


@pytest.mark.skipif(os.getenv("BRONZE_LIVE_TEST") != "1", reason="Requires running MinIO")
def test_live_upload_versioned_roundtrip(tmp_path, monkeypatch, capsys):
    storage = client()
    bucket = settings.minio_bucket_bronze
    setup_minio(storage, bucket)
    setup_minio(storage, bucket)
    assert storage.get_bucket_versioning(bucket).status == "Enabled"
    batch = "smoke-" + uuid4().hex
    source = tmp_path / "storage-check.csv"
    source.write_bytes(b"id,name\n1,bronze-storage-check\n")
    keys = []
    try:
        for domain in ("admissions", "hr"):
            key = object_key(domain, date(2026, 10, 15), batch, source.name)
            keys.append(key)
            monkeypatch.setattr(
                sys,
                "argv",
                [
                    "bronze",
                    "upload",
                    "--source",
                    domain,
                    "--file",
                    str(source),
                    "--batch-id",
                    batch,
                    "--date",
                    "2026-10-15",
                ],
            )
            main()
            stat = storage.stat_object(bucket, key)
            assert stat.version_id
            assert stat.version_id != "null"
            response = storage.get_object(bucket, key, version_id=stat.version_id)
            try:
                assert response.read() == source.read_bytes()
            finally:
                response.close()
                response.release_conn()
        assert "version_id" in capsys.readouterr().out
    finally:
        for key in keys:
            for obj in storage.list_objects(bucket, prefix=key, include_version=True):
                if obj.object_name == key:
                    storage.remove_object(bucket, key, version_id=obj.version_id)
