"""Phạm vi manager, hợp đồng HTTP và trường hợp Gold chưa sẵn sàng."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app.core.deps import get_current_user
from app.core.exceptions import AppException, app_exception_handler
from app.dependencies import get_db
from app.modules.manager import access
from app.modules.manager.router import router
from app.modules.manager.schemas import DatasetPage, MetricPage, SourceStats
from app.modules.staff.schemas import IssueStats
from app.repositories import manager_report_repo as repository


@pytest.fixture
def environment(monkeypatch):
    app = FastAPI()
    app.add_exception_handler(AppException, app_exception_handler)
    app.include_router(router, prefix="/api/v1")
    user = {"sub": "11", "roles": ["HR_MANAGER"]}
    roles = AsyncMock(return_value={"HR_MANAGER"})
    departments = AsyncMock(
        return_value=[
            {
                "department_id": 7,
                "department_code": "PNS",
                "department_name": "Nhân sự",
            }
        ]
    )
    db = AsyncMock()
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(access, "active_roles", roles)
    monkeypatch.setattr(access, "active_department_choices", departments)
    return TestClient(app), user, roles, departments, db


@pytest.mark.parametrize(
    ("role", "code", "domain"),
    [
        ("HR_MANAGER", "PNS", "hr"),
        ("ACADEMIC_MANAGER", "PDT", "academic"),
        ("ADMISSIONS_MANAGER", "PTS", "admissions"),
    ],
)
def test_context_uses_current_role_and_matching_department(environment, role, code, domain):
    client, user, roles, departments, _ = environment
    user["roles"] = [role]
    roles.return_value = {role}
    departments.return_value[0]["department_code"] = code
    result = client.get("/api/v1/manager/context")
    assert result.status_code == 200
    assert result.json()["default_domain"] == domain
    assert not result.json()["needs_department_assignment"]
    assert result.json()["data_layer"] == "GOLD"


@pytest.mark.parametrize("role", ["HR_STAFF", "ADMIN", "DATA_ADMIN", "VIEWER"])
def test_manager_endpoints_reject_other_roles(environment, role):
    client, user, _, _, db = environment
    user["roles"] = [role]
    assert client.get("/api/v1/manager/context").status_code == 403
    db.execute.assert_not_called()


def test_removed_role_in_db_is_rejected(environment):
    client, _, roles, _, _ = environment
    roles.return_value = {"HR_STAFF"}
    assert client.get("/api/v1/manager/context").status_code == 403


def test_missing_or_wrong_department_cannot_read_gold(environment):
    client, _, _, departments, db = environment
    departments.return_value[0]["department_code"] = "PDT"
    assert client.get("/api/v1/manager/context").json()["needs_department_assignment"]
    assert client.get("/api/v1/manager/overview").status_code == 403
    departments.return_value = []
    assert client.get("/api/v1/manager/datasets").status_code == 403
    db.execute.assert_not_called()


def test_request_cannot_expand_domain_scope(environment):
    client, _, _, _, db = environment
    assert client.get("/api/v1/manager/analytics?domain=academic").status_code == 403
    assert client.get("/api/v1/manager/issues?domain=admissions").status_code == 403
    db.execute.assert_not_called()


@pytest.mark.parametrize(
    "query",
    [
        "limit=101",
        "offset=-1",
        "dataset_id=0",
        "domain=unknown",
        "department_id=8",
        "from_date=2026-01-01",
    ],
)
def test_invalid_or_unsupported_report_filters_are_rejected(environment, query):
    client, *_ = environment
    assert client.get("/api/v1/manager/analytics?" + query).status_code == 422


@pytest.mark.parametrize(
    ("counts", "status"),
    [
        ({}, "NO_DATA"),
        ({"total": 1, "queued": 1}, "PENDING"),
        ({"total": 1, "failed": 1}, "PENDING"),
        ({"total": 2, "current": 1, "running": 1}, "PARTIAL"),
        ({"total": 1, "current": 1}, "READY"),
    ],
)
def test_overview_exposes_readiness_without_inventing_zero_kpis(
    environment, monkeypatch, counts, status
):
    client, *_ = environment
    monkeypatch.setattr(
        repository,
        "metrics",
        AsyncMock(return_value=MetricPage(items=[], total=0, limit=20, offset=0)),
    )
    monkeypatch.setattr(
        repository,
        "datasets",
        AsyncMock(return_value=DatasetPage(items=[], total=0, limit=1, offset=0)),
    )
    monkeypatch.setattr(repository, "source_stats", AsyncMock(return_value=SourceStats(**counts)))
    monkeypatch.setattr(repository, "last_updated", AsyncMock(return_value=None))
    monkeypatch.setattr(
        repository,
        "issue_stats",
        AsyncMock(return_value=IssueStats(total=0, assigned=0, pending_verification=0, verified=0)),
    )
    response = client.get("/api/v1/manager/overview")
    assert response.status_code == 200
    assert response.json()["status"] == status
    assert response.json()["kpis"]["items"] == []
    assert response.json()["last_updated_at"] is None


def test_unknown_dataset_returns_404(environment, monkeypatch):
    client, *_ = environment
    monkeypatch.setattr(
        repository,
        "datasets",
        AsyncMock(return_value=DatasetPage(items=[], total=0, limit=20, offset=0)),
    )
    assert client.get("/api/v1/manager/datasets/999").status_code == 404
    assert client.get("/api/v1/manager/datasets/0").status_code == 422


def test_missing_gold_schema_returns_controlled_error(environment):
    client, _, _, _, db = environment
    db.execute.side_effect = SQLAlchemyError("private database details")
    response = client.get("/api/v1/manager/overview")
    assert response.status_code == 503
    assert "private database details" not in response.text


def test_missing_authentication_returns_401():
    app = FastAPI()
    app.add_exception_handler(AppException, app_exception_handler)
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    assert TestClient(app).get("/manager/context").status_code == 401
