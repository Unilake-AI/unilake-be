"""Kiểm tra quyền và đầu ra của Reliability MVP cho ingestion."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.core.exceptions import AppException
from app.modules.ingestion import router
from app.modules.ingestion.execution import ingestion_read_domains


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
