"""Kiểm tra quyền API, lưu DB, chống liên kết nhầm và phân công chồng lấn."""

from __future__ import annotations

from collections.abc import Iterator
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Table, create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.roles import RoleCode
from app.core.security import create_access_token
from app.dependencies import get_db
from app.main import create_app
from app.modules.auth.models import LoginSession, Role, UserRole
from app.modules.datasources.models import DataLayer, Dataset
from app.modules.users.models import Assignment, Department, Employee, Position, User


@pytest.fixture
def client() -> Iterator[TestClient]:
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
            db.add(LoginSession(login_session_id=index, app_user_id=index, session_status="ACTIVE"))
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
        with Session(engine) as db, db.begin():
            # SQLite không cấp identity cho BIGINT; chỉ cấp ID ở adapter kiểm thử.
            @event.listens_for(db, "before_flush")
            def assign_identity(session, *_):
                for obj in session.new:
                    if isinstance(obj, Assignment) and obj.assignment_id is None:
                        obj.assignment_id = 10

            adapter = MagicMock()
            for name in ("get", "scalar", "scalars", "execute", "flush"):
                setattr(adapter, name, AsyncMock(side_effect=getattr(db, name)))
            adapter.add.side_effect = db.add
            yield adapter

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
