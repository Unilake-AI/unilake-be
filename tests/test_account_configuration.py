"""Kiểm tra quyền API, lưu DB, chống liên kết nhầm và phân công chồng lấn."""

from __future__ import annotations

from collections.abc import Iterator
from hashlib import sha256
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Table, create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.roles import RoleCode
from app.core.security import create_access_token, create_refresh_token
from app.dependencies import get_db
from app.main import create_app
from app.modules.auth.models import LoginSession, Role, UserRole
from app.modules.datasources.models import DataLayer, Dataset
from app.modules.users.models import Assignment, Department, Employee, Position, User


@pytest.fixture
def refresh_token() -> str:
    return create_refresh_token("2")


@pytest.fixture
def client(refresh_token: str) -> Iterator[TestClient]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        execution_options={"schema_translate_map": {"app": None}},
    )
    for model in (
        User,
        Employee,
        Department,
        Position,
        Assignment,
        Role,
        UserRole,
        LoginSession,
        DataLayer,
        Dataset,
    ):
        cast("Table", model.__table__).create(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE iam_event (event_id INTEGER PRIMARY KEY, actor_id INTEGER, "
            "action VARCHAR(40), target_id INTEGER, occurred_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
            "outcome VARCHAR(12), changes JSON, error_code VARCHAR(100))"
        )
    with Session(engine) as db, db.begin():
        for index, role in enumerate((RoleCode.ADMIN, RoleCode.DATA_ADMIN, RoleCode.HR_MANAGER), 1):
            db.add(Role(role_id=index, role_code=role, role_name=role))
            db.add(
                User(
                    app_user_id=index,
                    username=f"user{index}",
                    password_hash="secret",
                    is_active=True,
                )
            )
            db.add(UserRole(app_user_id=index, role_id=index))
            db.add(
                LoginSession(
                    login_session_id=index,
                    app_user_id=index,
                    session_status="ACTIVE",
                    refresh_token_hash=sha256(refresh_token.encode()).hexdigest()
                    if index == 2
                    else None,
                )
            )
        for index in (1, 2):
            db.add(
                Employee(
                    employee_id=index,
                    employee_code=f"NV{index}",
                    full_name=f"Nhân viên {index}",
                    employee_status="ACTIVE",
                )
            )
        db.add(
            Department(
                department_id=1,
                department_code="PNS",
                department_name="Nhân sự",
                department_type="ROOM",
                status="ACTIVE",
            )
        )
        db.add(
            Department(
                department_id=2,
                department_code="OLD",
                department_name="Đã đóng",
                department_type="ROOM",
                status="INACTIVE",
            )
        )
        db.add(Position(position_id=1, position_code="TP", position_name="Trưởng phòng"))
        db.add(DataLayer(data_layer_id=1, layer_code="BRONZE", layer_name="Bronze"))
        db.add(
            Dataset(
                dataset_id=1,
                dataset_code="HR",
                dataset_name="Nhân sự",
                domain="hr",
                data_layer_id=1,
                status="ACTIVE",
            )
        )
    application = create_app()

    def override() -> Iterator[MagicMock]:
        with Session(engine) as db:
            # SQLite không cấp identity cho BIGINT; chỉ cấp ID ở adapter kiểm thử.
            @event.listens_for(db, "before_flush")
            def assign_identity(session, *_):
                for obj in session.new:
                    if isinstance(obj, Assignment) and obj.assignment_id is None:
                        obj.assignment_id = 10
                    if isinstance(obj, User) and obj.app_user_id is None:
                        obj.app_user_id = 10

            adapter = MagicMock()
            for name in ("get", "scalar", "scalars", "execute", "flush", "rollback", "commit"):
                setattr(adapter, name, AsyncMock(side_effect=getattr(db, name)))
            adapter.add.side_effect = db.add
            adapter.add_all.side_effect = db.add_all
            try:
                yield adapter
                db.commit()
            except Exception:
                db.rollback()
                raise

    application.dependency_overrides[get_db] = override
    with TestClient(application) as api:
        yield api
    engine.dispose()


def headers(user_id: int = 1) -> dict[str, str]:
    role = {1: "ADMIN", 2: "DATA_ADMIN", 3: "HR_MANAGER"}[user_id]
    return {
        "Authorization": f"Bearer {create_access_token(str(user_id), claims={'roles': [role], 'sid': user_id})}"
    }


def test_account_link_persists_and_is_unique(client):
    endpoint = "/api/v1/users/3/employee"
    payload = {"employee_id": 1, "expected_employee_id": None}
    assert client.patch(endpoint, json=payload, headers=headers()).status_code == 204
    data = client.get("/api/v1/users", headers=headers()).json()
    assert data[2]["employee_id"] == 1
    assert "password_hash" not in data[2]
    assert (
        client.patch("/api/v1/users/2/employee", json=payload, headers=headers()).status_code == 409
    )
    assert (
        client.patch(
            endpoint, json={"employee_id": 2, "expected_employee_id": None}, headers=headers()
        ).status_code
        == 409
    )


@pytest.mark.parametrize("user_id", [2, 3])
def test_configuration_requires_admin(client, user_id):
    for url in (
        "/users",
        "/users/employees",
        "/users/organization",
        "/users/summary",
        "/users/audit",
        "/users/employees/1/assignments",
    ):
        assert client.get("/api/v1" + url, headers=headers(user_id)).status_code == 403
    assert (
        client.patch(
            "/api/v1/users/3/employee",
            json={"employee_id": 1, "expected_employee_id": None},
            headers=headers(user_id),
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/users/assignments",
            json={
                "employee_id": 1,
                "department_id": 1,
                "position_id": 1,
                "start_date": "2026-01-01",
            },
            headers=headers(user_id),
        ).status_code
        == 403
    )


def test_assignment_dates_conflicts_and_effective_manager_scope(client):
    client.patch(
        "/api/v1/users/3/employee",
        json={"employee_id": 1, "expected_employee_id": None},
        headers=headers(),
    )
    payload = {
        "employee_id": 1,
        "department_id": 1,
        "position_id": 1,
        "start_date": "2020-01-01",
        "is_primary": True,
    }
    assert (
        client.post(
            "/api/v1/users/assignments",
            json=payload | {"end_date": "2019-01-01"},
            headers=headers(),
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/users/assignments", json=payload | {"department_id": 2}, headers=headers()
        ).status_code
        == 422
    )
    assert (
        client.post("/api/v1/users/assignments", json=payload, headers=headers()).status_code == 201
    )
    assert (
        client.post("/api/v1/users/assignments", json=payload, headers=headers()).status_code == 409
    )
    row = client.get("/api/v1/users", headers=headers()).json()[2]
    assert row["departments"][0]["department_code"] == "PNS"
    rows = client.get("/api/v1/users/employees/1/assignments", headers=headers()).json()
    assert rows[0]["position_name"] == "Trưởng phòng"


def test_owner_permission_validation_and_stale_update(client):
    endpoint = "/api/v1/datasets/1/ownership"
    payload = {"owner_department_id": 1, "expected_owner_department_id": None}
    assert client.patch(endpoint, json=payload, headers=headers(3)).status_code == 403
    assert (
        client.patch(
            endpoint, json=payload | {"owner_department_id": 2}, headers=headers(2)
        ).status_code
        == 422
    )
    result = client.patch(endpoint, json=payload, headers=headers(2))
    assert result.status_code == 200, result.text
    assert result.json()["owner_department_id"] == 1
    assert client.patch(endpoint, json=payload, headers=headers(2)).status_code == 409


def test_account_access_and_audit(client):
    endpoint = "/api/v1/users"
    admin = headers()
    request = {
        "username": "dual_operator",
        "password": "test-password-2026",
        "roles": ["ADMIN", "DATA_ADMIN"],
    }
    assert client.post(endpoint, json=request, headers=headers(2)).status_code == 403
    created = client.post(endpoint, json=request, headers=admin)
    assert created.status_code == 201, created.text
    account_id = created.json()["user_id"]
    assert client.post(endpoint, json=request, headers=admin).status_code == 409

    account = client.get(endpoint, params={"search": "dual_operator"}, headers=admin).json()[0]
    assert account["roles"] == ["ADMIN", "DATA_ADMIN"]
    assert set(account).isdisjoint({"password", "password_hash"})
    patch = {
        "roles": ["DATA_ADMIN"],
        "is_active": False,
        "expected_roles": ["ADMIN", "DATA_ADMIN"],
        "expected_is_active": True,
    }
    access = f"{endpoint}/{account_id}/access"
    assert client.patch(access, json=patch, headers=headers(2)).status_code == 403
    assert client.patch(access, json=patch, headers=admin).status_code == 204
    assert client.patch(access, json=patch, headers=admin).status_code == 409
    patch["expected_roles"] = ["ADMIN"]
    assert client.patch(f"{endpoint}/1/access", json=patch, headers=admin).status_code == 409

    summary = client.get(f"{endpoint}/summary", headers=admin).json()
    assert summary["total"] == 4
    assert summary["active"] == 3
    audit = client.get(f"{endpoint}/audit", headers=admin).json()
    assert {
        (event["username"], event["action"])
        for event in audit
        if event["target_id"] == account_id and event["outcome"] == "SUCCESS"
    } == {("user1", "ACCOUNT_CREATED"), ("user1", "ACCOUNT_LOCKED"), ("user1", "ROLES_UPDATED")}
    assert any(event["action"] == "LOGIN" for event in audit)


def test_lock_and_unlock_does_not_restore_an_existing_session(client, refresh_token):
    endpoint = "/api/v1/users/2/access"
    expected = {"roles": ["DATA_ADMIN"], "expected_roles": ["DATA_ADMIN"]}
    lock = {**expected, "is_active": False, "expected_is_active": True}
    unlock = {**expected, "is_active": True, "expected_is_active": False}
    assert client.patch(endpoint, json=lock, headers=headers()).status_code == 204
    assert (
        client.get("/api/v1/datasets", headers=headers(2)).json()["error"]["code"]
        == "USER_INACTIVE"
    )
    refresh = "/api/v1/auth/refresh"
    request = {"refresh_token": refresh_token}
    assert client.post(refresh, json=request).json()["error"]["code"] == "SESSION_EXPIRED"
    assert client.patch(endpoint, json=unlock, headers=headers()).status_code == 204
    assert client.post(refresh, json=request).json()["error"]["code"] == "SESSION_EXPIRED"
    assert (
        client.get("/api/v1/datasets", headers=headers(2)).json()["error"]["code"]
        == "SESSION_EXPIRED"
    )


@pytest.mark.parametrize(
    "invalid",
    [
        {"roles": []},
        {"roles": ["ROLE_DOES_NOT_EXIST"]},
        {"password": {"nested": "private-credential-test"}},
    ],
)
def test_account_validation_never_echoes_secret(client, invalid):
    secret = "private-credential-test"
    response = client.post(
        "/api/v1/users",
        headers=headers(),
        json={"username": "invalid_account", "password": secret, "roles": ["ADMIN"], **invalid},
    )
    assert response.status_code == 422
    assert secret not in response.text
    assert all("input" not in violation for violation in response.json()["detail"])


def test_audit_records_sanitized_changes_and_rejections(client):
    base = "/api/v1/users"
    secret = "private-credential-test"
    payload = {"username": "audit_test", "password": secret, "roles": ["DATA_ADMIN"]}
    assert client.post(base, headers=headers(), json=payload).status_code == 201
    assert client.post(base, headers=headers(), json=payload).status_code == 409
    endpoint = f"{base}/2/access"
    update = {
        "roles": ["DATA_ADMIN", "HR_MANAGER"],
        "expected_roles": ["DATA_ADMIN"],
        "is_active": False,
        "expected_is_active": True,
    }
    assert client.patch(endpoint, json=update, headers=headers()).status_code == 204
    assert client.patch(endpoint, json=update, headers=headers()).status_code == 409
    audit = client.get(f"{base}/audit", headers=headers())
    assert audit.status_code == 200
    assert audit.headers["cache-control"] == "no-store"
    assert all(value not in audit.text for value in (secret, "password_hash", "refresh_token"))
    changes = {row["action"]: row["changes"] for row in audit.json() if row["outcome"] == "SUCCESS"}
    assert changes["ACCOUNT_CREATED"] == {"roles": ["DATA_ADMIN"], "is_active": True}
    assert changes["ROLES_UPDATED"] == {
        "roles": {"before": ["DATA_ADMIN"], "after": ["DATA_ADMIN", "HR_MANAGER"]}
    }
    assert changes["ACCOUNT_LOCKED"] == {"is_active": {"before": True, "after": False}}
    rejections = [row for row in audit.json() if row["outcome"] == "REJECTED"]
    assert {row["action"] for row in rejections} == {"ACCOUNT_CREATED", "ACCOUNT_ACCESS_UPDATED"}
    assert all(row["actor_id"] == 1 and row["changes"] is None for row in rejections)
    assert all(row["error_code"] == "ACCOUNT_CONFIGURATION_CONFLICT" for row in rejections)


@pytest.mark.parametrize(("roles", "active"), [(["DATA_ADMIN"], True), (["ADMIN"], False)])
def test_admin_cannot_remove_own_access(client, roles, active):
    response = client.patch(
        "/api/v1/users/1/access",
        headers=headers(),
        json={
            "roles": roles,
            "is_active": active,
            "expected_roles": ["ADMIN"],
            "expected_is_active": True,
        },
    )
    assert response.status_code == 409
    assert client.get("/api/v1/users", headers=headers()).status_code == 200
