"""Kiểm tra tài khoản tải file chỉ được truy cập kho tiếp nhận và đúng vai trò."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
from dotenv import dotenv_values

from app.core.config import settings
from app.infra.minio import client as clients
from app.infra.minio.upload_accounts import export_runtime, policy_for, prepare


@pytest.mark.parametrize("role", ["writer", "reader"])
def test_upload_clients_require_dedicated_credentials(monkeypatch, role):
    monkeypatch.setattr(settings, f"upload_{role}_access_key", "")
    constructor = Mock()
    monkeypatch.setattr(clients, "Minio", constructor)
    with pytest.raises(ValueError, match="CREDENTIALS_REQUIRED"):
        getattr(clients, f"upload_{role}_client")()
    constructor.assert_not_called()


def test_upload_permissions_never_include_bronze_or_deletion():
    for role in ("reader", "writer"):
        policy = policy_for(role, "ingestion-uploads")
        actions = {action for statement in policy["Statement"] for action in statement["Action"]}
        assert not any("Delete" in action for action in actions)
        assert ("s3:PutObject" in actions) == (role == "writer")
        assert all(
            "ingestion-uploads" in resource
            for statement in policy["Statement"]
            for resource in statement["Resource"]
        )
    with pytest.raises(ValueError, match="MUST_BE_SEPARATE"):
        policy_for("writer", settings.minio_bucket_bronze)


def test_export_upload_credentials_keeps_roles_separate(tmp_path: Path):
    path = tmp_path / ".env.upload-accounts.local"
    prepare(path)
    export_runtime(path)
    export_runtime(path)
    backend = dotenv_values(tmp_path / ".env.upload-backend.local")
    worker = dotenv_values(tmp_path / ".env.upload-worker.local")
    assert set(backend) == {"UPLOAD_WRITER_ACCESS_KEY", "UPLOAD_WRITER_SECRET_KEY"}
    assert set(worker) == {
        "UPLOAD_READER_ACCESS_KEY",
        "UPLOAD_READER_SECRET_KEY",
        "UPLOAD_CLEANUP_ACCESS_KEY",
        "UPLOAD_CLEANUP_SECRET_KEY",
    }
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        prepare(path)
