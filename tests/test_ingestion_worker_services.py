"""Kiểm tra worker xử lý hàng đợi và dừng đúng lúc khi không còn giữ được tác vụ."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.modules.ingestion.services import worker


@pytest.fixture
def environment(monkeypatch):
    db = AsyncMock()

    @asynccontextmanager
    async def session_factory():
        yield db

    repository = Mock()
    repository.recover_expired = AsyncMock()
    repository.claim = AsyncMock(return_value=None)
    repository.heartbeat_worker = AsyncMock()
    repository.expired_staging_inputs = AsyncMock(return_value=[])
    monkeypatch.setattr(worker, "IngestionRepository", lambda _db: repository)
    return SimpleNamespace(factory=session_factory, repository=repository)


@pytest.mark.asyncio
async def test_idle_worker_does_not_start_execution(environment, monkeypatch):
    execution = Mock()
    monkeypatch.setattr(worker, "IngestionExecutionService", execution)
    state = worker.WorkerState()

    assert await worker.run_once(state, environment.factory) is False

    assert state.current_job_id is None
    environment.repository.recover_expired.assert_awaited_once()
    execution.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("job_type", ["UPLOAD_V1", "EXECUTION_V1"])
async def test_worker_uses_correct_input_and_returns_to_idle(environment, monkeypatch, job_type):
    job = SimpleNamespace(ingestion_job_id=42, job_type=job_type)
    environment.repository.claim.return_value = job
    heartbeat_ids = []

    async def report(state, factory):
        assert factory is environment.factory
        heartbeat_ids.append(state.current_job_id)

    async def execute(actual_job, capture, store):
        assert actual_job is job
        assert store is worker.store_bronze
        if job_type == "UPLOAD_V1":
            source = capture()
            assert isinstance(source, worker.UploadCapture)
            assert source.job is job
        else:
            assert capture is worker.BronzeCapture

    execution = SimpleNamespace(execute=AsyncMock(side_effect=execute))
    monkeypatch.setattr(worker, "IngestionExecutionService", lambda _repo: execution)
    monkeypatch.setattr(worker, "report_worker_heartbeat", report)
    state = worker.WorkerState()

    assert await worker.run_once(state, environment.factory) is True

    assert state.current_job_id is None
    assert heartbeat_ids == [42, None]
    execution.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_lost_job_lease_cancels_execution_and_clears_current_job(environment, monkeypatch):
    environment.repository.claim.return_value = SimpleNamespace(
        ingestion_job_id=42, job_type="UPLOAD_V1"
    )
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def execute(*_args):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    async def lost_lease(_job_id, _factory):
        await started.wait()
        raise RuntimeError("INGESTION_LEASE_LOST")

    monkeypatch.setattr(
        worker, "IngestionExecutionService", lambda _repo: SimpleNamespace(execute=execute)
    )
    monkeypatch.setattr(worker, "report_worker_heartbeat", AsyncMock())
    monkeypatch.setattr(worker, "job_heartbeat", lost_lease)
    state = worker.WorkerState()

    with pytest.raises(RuntimeError, match="INGESTION_LEASE_LOST"):
        await asyncio.wait_for(worker.run_once(state, environment.factory), timeout=2)

    assert stopped.is_set()
    assert state.current_job_id is None


@pytest.mark.asyncio
async def test_failed_database_heartbeat_does_not_mark_worker_healthy(
    environment, monkeypatch, tmp_path
):
    health_file = tmp_path / "heartbeat"
    monkeypatch.setattr(worker, "HEALTH_FILE", health_file)
    environment.repository.heartbeat_worker.side_effect = SQLAlchemyError("Không thể kết nối")

    with pytest.raises(SQLAlchemyError):
        await worker.report_worker_heartbeat(worker.WorkerState(), environment.factory)

    assert not health_file.exists()


@pytest.mark.asyncio
async def test_cleanup_removes_only_pinned_versions_in_upload_bucket(environment, monkeypatch):
    bucket = settings.minio_bucket_uploads
    environment.repository.expired_staging_inputs.return_value = [
        {"uri": f"s3://{bucket}/incoming/7/source.csv", "version_id": "v1"},
        {"uri": "s3://another-bucket/source.csv", "version_id": "v2"},
        {"uri": f"s3://{bucket}/incoming/7/source.csv"},
    ]
    storage = Mock()
    monkeypatch.setattr(worker, "upload_cleanup_client", lambda: storage)

    await worker.cleanup_staging(environment.factory)

    storage.remove_object.assert_called_once_with(bucket, "incoming/7/source.csv", version_id="v1")
