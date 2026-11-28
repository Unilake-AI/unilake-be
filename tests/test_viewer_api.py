"""Kiểm tra quyền VIEWER và hợp đồng đọc trên PostgreSQL đã chạy migration."""

from __future__ import annotations

import os
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.core import deps
from app.core.security import create_access_token
from app.dependencies import get_db
from app.main import create_app

PREFIX = "/api/v1/viewer"
ENDPOINTS = (
    "/context",
    "/overview",
    "/domains/hr/overview",
    "/domains/hr/analytics",
    "/datasets",
    "/datasets/1",
    "/quality/overview",
    "/quality/issues",
)


def auth_headers(role: str) -> dict[str, str]:
    token = create_access_token("11", claims={"sid": "22", "roles": [role]})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def app():
    return create_app()


@pytest.mark.parametrize(
    ("token_role", "db_roles", "expected"),
    [
        (None, set(), 401),
        ("HR_MANAGER", {"HR_MANAGER"}, 403),
        ("HR_STAFF", {"HR_STAFF"}, 403),
        ("ADMIN", {"ADMIN"}, 403),
        ("VIEWER", {"HR_STAFF"}, 401),
    ],
)
def test_viewer_routes_require_current_db_role(app, monkeypatch, token_role, db_roles, expected):
    db = AsyncMock()
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(deps, "user_auth_state", AsyncMock(return_value=(True, "ACTIVE", db_roles)))
    with TestClient(app) as client:
        headers = auth_headers(token_role) if token_role else {}
        for endpoint in ENDPOINTS:
            response = client.get(PREFIX + endpoint, headers=headers)
            assert response.status_code == expected
            if token_role == "VIEWER":
                assert response.json()["error"]["code"] == "TOKEN_STALE"
    db.execute.assert_not_called()


def test_context_and_filter_validation(app, monkeypatch):
    db = AsyncMock()
    app.dependency_overrides[get_db] = lambda: db
    state = AsyncMock(return_value=(True, "ACTIVE", {"VIEWER"}))
    monkeypatch.setattr(deps, "user_auth_state", state)
    with TestClient(app, headers=auth_headers("VIEWER")) as client:
        response = client.get(PREFIX + "/context")
        assert response.status_code == 200
        assert response.json()["scope"] == "UNIVERSITY"
        assert [item["domain"] for item in response.json()["domains"]] == [
            "hr",
            "academic",
            "admissions",
        ]
        state.assert_awaited_once_with(db, 11, 22)
        for path in (
            "/domains/unknown/overview",
            "/domains/hr/analytics?domain=academic",
            "/domains/hr/analytics?dimension_value=x",
            "/datasets?limit=101",
            "/datasets?domain=unknown",
            "/datasets?dataset_id=9223372036854775808",
            "/datasets/0",
            "/quality/issues?status=UNKNOWN",
            "/quality/issues?offset=-1",
        ):
            assert client.get(PREFIX + path).status_code == 422
    db.execute.assert_not_called()


@pytest.fixture
def viewer_db():
    # URL dành cho DB kiểm thử đã chạy infra/postgres/migrations/upgrade.psql.
    url = os.getenv("VIEWER_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires migrated PostgreSQL test database (VIEWER_TEST_DATABASE_URL)")
    engine = create_engine(url)
    try:
        with engine.connect() as connection, connection.begin() as transaction:
            assert connection.scalar(text("SELECT count(*) FROM app.dataset")) == 0
            yield connection
            transaction.rollback()
    finally:
        engine.dispose()


@pytest.fixture
def viewer_data(viewer_db):
    def insert_dataset(code, domain, status="ACTIVE"):
        return viewer_db.scalar(
            text("""
                INSERT INTO app.dataset(dataset_code, dataset_name, domain, data_layer_id, status)
                SELECT :code, :code, :domain, data_layer_id, :status
                FROM app.data_layer WHERE layer_code='SILVER'
                RETURNING dataset_id
            """),
            {"code": code, "domain": domain, "status": status},
        )

    datasets = {
        "hr": insert_dataset("HR", "hr"),
        "academic": insert_dataset("ACADEMIC", "academic"),
        "admissions": insert_dataset("ADMISSIONS", "admissions"),
        "inactive": insert_dataset("PRIVATE_INACTIVE", "hr", "INACTIVE"),
        "unsupported": insert_dataset("PRIVATE_UNSUPPORTED", "other"),
        "hr_without_scope": insert_dataset("HR_WAITING", "hr"),
    }

    def gold(scope, domain, value, *, replaced=False):
        job = str(uuid4())
        snapshot = str(uuid4())
        params = {
            "scope": scope,
            "domain": domain,
            "dataset": datasets[domain],
            "job": job,
            "snapshot": snapshot,
            "value": value,
        }
        viewer_db.execute(
            text("""
                INSERT INTO app.gold_scope(scope, dataset_id, domain, silver_snapshot_id, active_job_id)
                VALUES(:scope, :dataset, :domain, :snapshot, :job)
            """),
            params,
        )
        viewer_db.execute(
            text("""
                INSERT INTO app.gold_job(job_id, scope, dataset_id, domain, generation,
                                         silver_snapshot_id, status, finished_at)
                VALUES(:job, :scope, :dataset, :domain, 1, :snapshot, 'SUCCEEDED', now())
            """),
            params,
        )
        viewer_db.execute(
            text("""
                INSERT INTO gold.aggregate(job_id, entity, metric, dimensions, value)
                VALUES(:job, 'entity', 'record_count', '{}', :value)
            """),
            params,
        )
        if replaced:
            params["snapshot"] = str(uuid4())
            viewer_db.execute(
                text("""
                    UPDATE app.gold_scope SET silver_snapshot_id=:snapshot, generation=2
                    WHERE scope=:scope
                """),
                params,
            )
            params["job"] = str(uuid4())
            viewer_db.execute(
                text("""
                    INSERT INTO app.gold_job(job_id, scope, dataset_id, domain, generation,
                                             silver_snapshot_id, status, finished_at)
                    VALUES(:job, :scope, :dataset, :domain, 2, :snapshot, 'FAILED', now())
                """),
                params,
            )
        return job

    gold("hr-a", "hr", 1000)
    hr_sync_failed = gold("hr-b", "hr", 900)
    gold("academic-replaced", "academic", 18500, replaced=True)
    gold("admissions-current", "admissions", 42)
    viewer_db.execute(
        text("UPDATE app.gold_sync_job SET status='FAILED' WHERE gold_job_id=:job"),
        {"job": hr_sync_failed},
    )
    table = viewer_db.scalar(
        text("""
            INSERT INTO app.catalog_table(dataset_id, table_name)
            VALUES(:dataset, 'employee') RETURNING catalog_table_id
        """),
        {"dataset": datasets["hr"]},
    )
    viewer_db.execute(
        text("""
            INSERT INTO app.catalog_column(catalog_table_id, column_name, data_type, is_nullable)
            VALUES(:table, 'employee_status', 'string', false)
        """),
        {"table": table},
    )

    department = viewer_db.scalar(
        text("SELECT department_id FROM app.department WHERE department_code='PNS'")
    )
    if department is None:
        department = viewer_db.scalar(
            text("""
                INSERT INTO app.department(department_code, department_name, department_type)
                VALUES('VIEWER_TEST','Test','OFFICE') RETURNING department_id
            """)
        )
    user = viewer_db.scalar(
        text("""
            INSERT INTO app.app_user(username, password_hash) VALUES('viewer_test', 'unused')
            RETURNING app_user_id
        """)
    )
    for key, domain, status in (
        ("hr", "hr", "ASSIGNED"),
        ("hr", "hr", "ASSIGNED"),
        ("academic", "academic", "VERIFIED"),
        ("admissions", "admissions", "PENDING_VERIFICATION"),
        ("inactive", "hr", "ASSIGNED"),
        ("unsupported", "other", "ASSIGNED"),
    ):
        viewer_db.execute(
            text("""
                INSERT INTO app.data_quality_issue(issue_id, dataset_id, department_id, domain,
                                                    title, description, status, created_by,
                                                    created_at, updated_at)
                VALUES(:id, :dataset, :department, :domain,
                       'Private title', 'person@example.test', :status, :user, now(), now())
            """),
            {
                "id": str(uuid4()),
                "dataset": datasets[key],
                "department": department,
                "domain": domain,
                "status": status,
                "user": user,
            },
        )

    return datasets


def test_viewer_reports_use_current_gold_and_safe_projections(
    app, monkeypatch, viewer_db, viewer_data
):
    db = SimpleNamespace(execute=AsyncMock(side_effect=viewer_db.execute))
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(
        deps, "user_auth_state", AsyncMock(return_value=(True, "ACTIVE", {"VIEWER"}))
    )
    with TestClient(app, headers=auth_headers("VIEWER")) as client:

        def get(path):
            response = client.get(PREFIX + path)
            assert response.status_code == 200, response.text
            return response.json()

        overview = get("/overview")
        assert overview["summary"] == {"datasets": 4, "sources": 4, "ready_domains": 1}
        for domain, status in (("hr", "PARTIAL"), ("academic", "PENDING"), ("admissions", "READY")):
            item = get(f"/domains/{domain}/overview")
            assert item in overview["domains"]
            assert item["status"] == status
            if status != "READY":
                assert item["highlight_metric"] is None
                assert item["message"]
        assert "18500" not in str(overview)
        assert overview["domains"][2]["highlight_metric"]["value"] == 42

        academic = get("/domains/academic/analytics")
        assert academic == {"items": [], "total": 0, "limit": 20, "offset": 0}
        hr_analytics = f"/domains/hr/analytics?dataset_id={viewer_data['hr']}"
        page = get(hr_analytics + "&limit=1")
        next_page = get(hr_analytics + "&limit=1&offset=1")
        assert page["total"] == next_page["total"] == 2
        assert [page["items"][0]["value"], next_page["items"][0]["value"]] == [1000, 900]
        assert get(hr_analytics + "&offset=999")["total"] == 2
        assert get(f"/domains/admissions/analytics?dataset_id={viewer_data['hr']}")["total"] == 0
        assert get("/domains/admissions/analytics")["items"][0]["value"] == 42

        assert get("/datasets")["total"] == 4
        page = get("/datasets?domain=hr&limit=1&offset=1")
        assert page["total"] == 2
        assert page["items"][0]["dataset_id"] == viewer_data["hr_without_scope"]
        detail = get(f"/datasets/{viewer_data['hr']}")
        assert detail["tables"][0]["columns"][0]["name"] == "employee_status"
        for hidden_id in (viewer_data["inactive"], viewer_data["unsupported"], 999999):
            response = client.get(f"{PREFIX}/datasets/{hidden_id}")
            assert response.status_code == 404
            assert response.json()["error"]["code"] == "DATASET_NOT_FOUND"

        quality = get("/quality/overview")
        assert quality["quality"] == {
            "total": 4,
            "assigned": 2,
            "pending_verification": 1,
            "verified": 1,
        }
        assert quality["publication"] == {
            "total": 4,
            "current": 3,
            "not_current": 1,
            "gold_job_failed": 1,
            "sync_failed": 1,
            "datasets_without_scope": 1,
        }
        assert quality["freshness"]["academic"] is None
        assert datetime.fromisoformat(quality["freshness"]["admissions"]).utcoffset() is not None

        issues_path = f"/quality/issues?domain=hr&status=ASSIGNED&dataset_id={viewer_data['hr']}"
        first = get(issues_path + "&limit=1")
        second = get(issues_path + "&limit=1&offset=1")
        assert first["total"] == second["total"] == 2
        assert first["items"][0]["issue_id"] != second["items"][0]["issue_id"]
        assert get(issues_path + "&offset=2")["items"] == []
        assert get("/quality/issues?domain=hr&status=VERIFIED")["total"] == 0
        assert set(first["items"][0]) == {
            "issue_id",
            "dataset_id",
            "department_id",
            "domain",
            "created_at",
            "updated_at",
            "status",
        }

        viewer_db.execute(
            text("UPDATE app.dataset SET status='INACTIVE' WHERE dataset_id=:dataset"),
            {"dataset": viewer_data["hr_without_scope"]},
        )
        ready_hr = get("/domains/hr/overview")
        assert ready_hr["status"] == "READY"
        assert ready_hr["highlight_metric"] is None

        viewer_db.execute(
            text("UPDATE app.dataset SET status='INACTIVE' WHERE domain='admissions'")
        )
        missing = get("/domains/admissions/overview")
        assert missing["status"] == "NO_DATA"
        assert missing["highlight_metric"] is None
        assert missing["last_published_at"] is None
