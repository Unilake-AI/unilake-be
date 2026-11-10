"""Kiểm tra quyền và đầu ra của Reliability MVP cho ingestion."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.core.exceptions import AppException
from app.modules.ingestion import router
from app.modules.ingestion.execution import ingestion_read_domains
from app.repositories.ingestion_repo import IngestionRepository


@pytest.mark.asyncio
async def test_stats_uses_staff_domain(monkeypatch):
    repository = Mock()
    repository.stats = AsyncMock(
        return_value={"total": 2, "queued": 1, "running": 0, "success": 1, "failed": 0}
    )
    monkeypatch.setattr(router, "IngestionRepository", lambda _db: repository)

    result = await router.job_stats(AsyncMock(), {"roles": ["HR_STAFF"]})

    assert result["total"] == 2
    repository.stats.assert_awaited_once_with(frozenset({"hr"}))


@pytest.mark.asyncio
async def test_retry_is_limited_to_data_administrators(monkeypatch):
    repository = Mock()
    repository.retry = AsyncMock(return_value=SimpleNamespace(ingestion_job_id=106))
    monkeypatch.setattr(router, "IngestionRepository", lambda _db: repository)

    result = await router.retry_job(105, AsyncMock(), {"sub": "8", "roles": ["DATA_ADMIN"]})

    assert result.ingestion_job_id == 106
    repository.retry.assert_awaited_once_with(105, "8")


def test_viewer_cannot_read_ingestion_jobs():
    with pytest.raises(AppException) as exc:
        ingestion_read_domains({"roles": ["VIEWER"]})

    assert exc.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("last_run", ["old", "recent", "missing", "running"])
async def test_staging_retention_requires_complete_chain_timestamps(last_run):
    before = datetime(2026, 11, 10)
    old = before - timedelta(days=1)
    evidence = {"uri": "s3://ingestion-uploads/incoming/7/file.csv"}
    jobs = [
        SimpleNamespace(
            root_job_id=105,
            ingestion_job_id=105,
            status="FAILED",
            finished_at=old,
            created_at=old,
            upload_input=evidence,
        ),
        SimpleNamespace(
            root_job_id=105,
            ingestion_job_id=106,
            status="RUNNING" if last_run == "running" else "SUCCESS",
            finished_at=None
            if last_run == "missing"
            else (before + timedelta(hours=1) if last_run == "recent" else old),
            created_at=None if last_run == "missing" else old,
            upload_input=evidence,
        ),
    ]
    db = AsyncMock()
    db.scalars.return_value = jobs

    result = await IngestionRepository(db).expired_staging_inputs(before)

    assert result == ([evidence] if last_run == "old" else [])
