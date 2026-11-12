from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.exceptions import AppException, app_exception_handler
from app.dependencies import get_db
from app.modules.pipeline.routers.data_quality_issues import _actor, router
from app.modules.staff.services import quality_issues as api


@pytest.mark.parametrize(
    ("assignee_id", "actor"),
    [
        (None, api.IssueActor(42, False, frozenset({7}), frozenset({"hr"}))),
        (41, api.IssueActor(42, False, frozenset({7}), frozenset({"hr"}))),
        (41, api.IssueActor(42, True, frozenset({7}), frozenset({"hr"}))),
        (42, api.IssueActor(42, False, frozenset({8}), frozenset({"hr"}))),
    ],
)
def test_resolution_requires_current_assignee_with_active_department_scope(
    monkeypatch, assignee_id, actor
):
    load = AsyncMock(
        return_value={
            "assignee_user_id": assignee_id,
            "department_id": 7,
            "domain": "hr",
            "status": "ASSIGNED",
        }
    )
    execute = AsyncMock()
    monkeypatch.setattr(api, "_issue", load)
    monkeypatch.setattr(api.quality_issue_repo, "update_issue", execute)
    app = FastAPI()
    app.add_exception_handler(AppException, app_exception_handler)
    app.include_router(router)
    app.dependency_overrides[_actor] = lambda: actor
    app.dependency_overrides[get_db] = lambda: None

    with TestClient(app) as client:
        response = client.post(
            f"/data-quality-issues/{uuid4()}/resolution",
            json={"ingestion_job_id": 1, "note": "Đã sửa"},
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
    assert load.call_args.kwargs["lock"] is True
    execute.assert_not_called()
