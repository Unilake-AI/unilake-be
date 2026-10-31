"""Kiểm tra metadata UDL-141 qua writer, không cần kết nối MinIO thật."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.bronze_capture import store_bronze
from app.core.config import settings
from app.modules.ingestion.contracts import BronzeWriteContext, CapturedArtifact


def storage_for(data: bytes) -> Mock:
    """Mô phỏng metadata do S3 trả về với tên header không phân biệt hoa/thường."""
    storage = Mock()
    storage.get_bucket_versioning.return_value.status = "Enabled"
    storage.fput_object.return_value.version_id = "version-1"
    storage.get_object.return_value.stream.return_value = [data]

    def uploaded_metadata(*args: object, **kwargs: object) -> SimpleNamespace:
        metadata = storage.fput_object.call_args.kwargs["metadata"]
        return SimpleNamespace(
            metadata={f"X-Amz-Meta-{name}": value for name, value in metadata.items()}
        )

    storage.stat_object.side_effect = uploaded_metadata
    return storage


@pytest.mark.parametrize("domain", ["admissions", "academic", "hr"])
@pytest.mark.parametrize("aware", [False, True])
def test_metadata_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, domain: str, aware: bool
) -> None:
    """Ba domain đều giữ nguyên bytes, có metadata và đọc lại đúng object version."""
    data = b"id,name\r\n001,Example\r\n"
    path = tmp_path / "input.csv"
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    artifact = CapturedArtifact(path, 1, digest, "source-version")
    capture_time = datetime(2026, 10, 18, 1, 30)
    if aware:
        capture_time = datetime(2026, 10, 18, 8, 30, tzinfo=timezone(timedelta(hours=7)))
    job = BronzeWriteContext(42, datetime(2026, 10, 17, 18), 7, capture_time)
    storage = storage_for(data)
    verifier = Mock()
    verifier.stat_object.side_effect = storage.stat_object.side_effect
    verifier.get_object.return_value = storage.get_object.return_value
    monkeypatch.setattr("app.bronze_capture.bronze_writer_client", lambda: storage)
    monkeypatch.setattr("app.bronze_capture.bronze_verifier_client", lambda: verifier)

    result = store_bronze(artifact, domain, job)

    expected = {
        "sha256": digest,
        "source-system-id": "7",
        "ingestion-timestamp": "2026-10-18T01:30:00+00:00",
        "batch-id": "job-42",
    }
    key = f"{domain}/2026/10/18/job-42/input.csv"
    storage.fput_object.assert_called_once_with(
        settings.minio_bucket_bronze, key, str(path), metadata=expected
    )
    verifier.stat_object.assert_called_once_with(
        settings.minio_bucket_bronze, key, version_id="version-1"
    )
    verifier.get_object.assert_called_once_with(
        settings.minio_bucket_bronze, key, version_id="version-1"
    )
    assert result["metadata"] == expected
    assert result["source_version_id"] == "source-version"
    assert path.read_bytes() == data
    storage.get_object.assert_not_called()
    storage.stat_object.assert_not_called()
    verifier.fput_object.assert_not_called()
    storage.get_object.return_value.close.assert_called_once()
    storage.get_object.return_value.release_conn.assert_called_once()


@pytest.mark.parametrize(
    "missing", ["source-system-id", "ingestion-timestamp", "batch-id", "sha256"]
)
def test_missing_metadata_blocks_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    """Upload thành công nhưng thiếu provenance thì không trả receipt thành công."""
    path = tmp_path / "input.csv"
    path.write_bytes(b"id\n1\n")
    artifact = CapturedArtifact(path, 1, hashlib.sha256(path.read_bytes()).hexdigest())
    job = BronzeWriteContext(42, datetime.now(UTC), 7, datetime.now(UTC))
    storage = storage_for(path.read_bytes())

    def incomplete_metadata(*args: object, **kwargs: object) -> SimpleNamespace:
        metadata = storage.fput_object.call_args.kwargs["metadata"]
        return SimpleNamespace(
            metadata={f"x-amz-meta-{k}": v for k, v in metadata.items() if k != missing}
        )

    storage.stat_object.side_effect = incomplete_metadata
    monkeypatch.setattr("app.bronze_capture.bronze_writer_client", lambda: storage)
    monkeypatch.setattr("app.bronze_capture.bronze_verifier_client", lambda: storage)
    with pytest.raises(RuntimeError, match="BRONZE_METADATA_MISMATCH"):
        store_bronze(artifact, "admissions", job)
