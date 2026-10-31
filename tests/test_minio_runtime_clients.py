"""Kiểm tra runtime dùng SA đúng vai trò và không fallback sang credentials admin."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
from dotenv import dotenv_values

from app.core.config import settings
from app.infra.minio import client as clients
from app.infra.minio.service_accounts import export_runtime, prepare, prepare_source


@pytest.mark.parametrize("role", ["writer", "verifier", "reader"])
def test_runtime_requires_its_own_credentials(monkeypatch: pytest.MonkeyPatch, role: str) -> None:
    monkeypatch.setattr(settings, f"bronze_{role}_access_key", "")
    constructor = Mock()
    monkeypatch.setattr(clients, "Minio", constructor)
    with pytest.raises(ValueError, match="CREDENTIALS_REQUIRED"):
        getattr(clients, f"bronze_{role}_client")()
    constructor.assert_not_called()


@pytest.mark.parametrize("role", ["writer", "verifier", "reader"])
def test_client_uses_scoped_keys(monkeypatch: pytest.MonkeyPatch, role: str) -> None:
    monkeypatch.setattr(settings, f"bronze_{role}_access_key", f"test-{role}")
    monkeypatch.setattr(settings, f"bronze_{role}_secret_key", "test-secret")
    constructor = Mock()
    monkeypatch.setattr(clients, "Minio", constructor)
    getattr(clients, f"bronze_{role}_client")()
    assert constructor.call_args.kwargs["access_key"] == f"test-{role}"


def test_source_reader_never_uses_bronze_or_root_for_external_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "source_minio_access_key", "")
    constructor = Mock()
    monkeypatch.setattr(clients, "Minio", constructor)
    with pytest.raises(ValueError, match="MINIO_SOURCE_READER_CREDENTIALS_REQUIRED"):
        clients.source_reader_client("external-source")
    constructor.assert_not_called()


def test_export_runtime_keeps_admin_and_writer_out_of_backend(tmp_path: Path) -> None:
    path = tmp_path / ".env.bronze-accounts.local"
    prepare(path)
    prepare_source(tmp_path / ".env.source-reader.local")
    export_runtime(path)
    backend = dotenv_values(tmp_path / ".env.bronze-backend.local")
    worker = dotenv_values(tmp_path / ".env.bronze-worker.local")
    silver_backend = dotenv_values(tmp_path / ".env.silver-backend.local")
    assert "BRONZE_WRITER_SECRET_KEY" not in backend
    assert "BRONZE_VERIFIER_SECRET_KEY" not in backend
    assert "BRONZE_WRITER_SECRET_KEY" in worker
    assert "BRONZE_VERIFIER_SECRET_KEY" in worker
    for values in (backend, worker):
        assert "MINIO_ACCESS_KEY" not in values
        assert "MINIO_SECRET_KEY" not in values
        assert "SOURCE_MINIO_SECRET_KEY" in values
        assert "BRONZE_READER_SECRET_KEY" in values
        assert "SILVER_WRITER_SECRET_KEY" not in backend
        assert "SILVER_WRITER_SECRET_KEY" in silver_backend


def test_silver_writer_client_requires_its_scoped_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "silver_writer_access_key", "")
    constructor = Mock()
    monkeypatch.setattr(clients, "Minio", constructor)
    with pytest.raises(ValueError, match="MINIO_SILVER_WRITER_CREDENTIALS_REQUIRED"):
        clients.silver_writer_client()
    constructor.assert_not_called()
