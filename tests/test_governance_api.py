"""Kiểm tra API với JWT thật và repository chạy SQL trên DB SQLite cô lập."""

from __future__ import annotations

from collections.abc import Iterator
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Table, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.roles import RoleCode
from app.core.security import create_access_token
from app.dependencies import get_db
from app.main import create_app
from app.modules.auth.models import LoginSession, Role, UserRole
from app.modules.governance.models import CatalogMetadata
from app.modules.users.models import User

CATALOG = "/api/v1/governance/catalog"
DETAIL = f"{CATALOG}/demo/test_students"
PAYLOAD = {
    "domain_id": "demo",
    "dataset": "test_students",
    "layer": "silver",
    "schema_version": "1.0",
    "description": "Dữ liệu sinh viên",
    "fields": [{"name": "student_id", "data_type": "string", "nullable": False}],
}


@pytest.fixture
def client(monkeypatch) -> Iterator[TestClient]:
    # Dùng SQL thật để kiểm tra UPSERT, unique key và khả năng đọc lại qua request mới.
    # Bỏ schema app riêng cho SQLite; không kết nối hay sửa database của người dùng.
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        execution_options={"schema_translate_map": {"app": None}},
    )
    cast("Table", CatalogMetadata.__table__).create(engine)
    cast("Table", Role.__table__).create(engine)
    cast("Table", User.__table__).create(engine)
    cast("Table", UserRole.__table__).create(engine)
    cast("Table", LoginSession.__table__).create(engine)
    with Session(engine) as session, session.begin():
        for idx, role_code in enumerate(RoleCode, start=1):
            session.add(Role(role_id=idx, role_code=role_code.value, role_name=role_code.value))
            session.add(
                User(
                    app_user_id=idx,
                    username=f"user_{role_code.value.lower()}",
                    password_hash="hash",
                    is_active=True,
                )
            )
            session.add(UserRole(app_user_id=idx, role_id=idx))
            session.add(
                LoginSession(login_session_id=idx, app_user_id=idx, session_status="ACTIVE")
            )
        no_role_id = len(RoleCode) + 1
        session.add(
            User(
                app_user_id=no_role_id,
                username="user_no_role",
                password_hash="hash",
                is_active=True,
            )
        )
        session.add(
            LoginSession(
                login_session_id=no_role_id, app_user_id=no_role_id, session_status="ACTIVE"
            )
        )
        session.add(
            CatalogMetadata(
                domain_id="demo",
                dataset="legacy_students",
                metadata_payload=PAYLOAD | {"dataset": "legacy_students"},
            )
        )
    from tests.fixtures.registry import sample_registry

    monkeypatch.setattr(
        "app.modules.governance.catalog_service.get_domain_registry", sample_registry
    )
    application = create_app()

    def override_db() -> Iterator[MagicMock]:
        with Session(engine) as session, session.begin():
            db = MagicMock()
            db.scalars = AsyncMock(side_effect=session.scalars)
            db.execute = AsyncMock(side_effect=session.execute)
            yield db

    application.dependency_overrides[get_db] = override_db
    with TestClient(application) as api:
        yield api
    engine.dispose()


_ROLE_USER_IDS = {role.value: str(idx) for idx, role in enumerate(RoleCode, start=1)}
_NO_ROLE_USER_ID = str(len(RoleCode) + 1)


def headers(role: str = "DATA_ADMIN") -> dict[str, str]:
    user_id = _ROLE_USER_IDS.get(role, _NO_ROLE_USER_ID)
    token = create_access_token(
        user_id, claims={"roles": [role] if role else [], "sid": int(user_id)}
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("params", [{}, {"domain_id": "demo"}])
def test_data_admin_can_read_catalog(client: TestClient, params: dict[str, str]) -> None:
    response = client.get(CATALOG, params=params, headers=headers())
    assert response.status_code == 200
    entries = response.json()
    assert entries
    assert all("fields" in entry for entry in entries)
    assert all(
        entry["tables"]
        == [
            {
                "name": entry["dataset"],
                "description": entry["description"],
                "columns": entry["fields"],
            }
        ]
        for entry in entries
    )
    if params:
        assert all(entry["domain_id"] == "demo" for entry in entries)


@pytest.mark.parametrize(
    "role",
    [
        "ADMIN",
        "VIEWER",
        "ADMISSIONS_MANAGER",
        "ADMISSIONS_STAFF",
        "ACADEMIC_MANAGER",
        "ACADEMIC_STAFF",
        "HR_MANAGER",
        "HR_STAFF",
        "",
    ],
)
@pytest.mark.parametrize(
    ("method", "path"),
    [("get", CATALOG), ("get", DETAIL), ("post", CATALOG), ("patch", DETAIL), ("delete", DETAIL)],
)
def test_other_roles_cannot_access_catalog(
    client: TestClient, role: str, method: str, path: str
) -> None:
    response = client.request(method, path, headers=headers(role))
    assert response.status_code == 403


@pytest.mark.parametrize("auth_headers", [{}, {"Authorization": "Bearer invalid-token"}])
@pytest.mark.parametrize(
    ("method", "path"),
    [("get", CATALOG), ("get", DETAIL), ("post", CATALOG), ("patch", DETAIL), ("delete", DETAIL)],
)
def test_catalog_requires_valid_token(
    client: TestClient, auth_headers: dict[str, str], method: str, path: str
) -> None:
    assert client.request(method, path, headers=auth_headers).status_code == 401


def test_catalog_crud_persists_across_requests(client: TestClient) -> None:
    auth = headers()
    created = client.post(CATALOG, json=PAYLOAD, headers=auth)
    assert created.status_code == 201
    assert client.get(DETAIL, headers=auth).json() == created.json()
    assert client.post(CATALOG, json=PAYLOAD, headers=auth).status_code == 409
    updated = client.patch(DETAIL, json={"description": "Mô tả mới"}, headers=auth)
    assert updated.status_code == 200
    assert updated.json()["fields"] == created.json()["fields"]
    assert client.get(DETAIL, headers=auth).json()["description"] == "Mô tả mới"
    replaced = client.patch(DETAIL, json={"fields": []}, headers=auth)
    assert replaced.status_code == 200
    assert replaced.json()["fields"] == []
    assert client.delete(DETAIL, headers=auth).status_code == 204
    assert client.get(DETAIL, headers=auth).status_code == 404
    assert client.delete(DETAIL, headers=auth).status_code == 404
    assert all(
        entry["dataset"] != "test_students" for entry in client.get(CATALOG, headers=auth).json()
    )
    assert client.post(CATALOG, json=PAYLOAD, headers=auth).status_code == 201


def test_static_metadata_can_be_updated_and_deleted(client: TestClient) -> None:
    auth = headers()
    path = f"{CATALOG}/demo/demo_students"
    original = client.get(path, headers=auth).json()
    assert client.post(CATALOG, json=original, headers=auth).status_code == 409
    assert client.patch(path, json={"description": "Đã chỉnh sửa"}, headers=auth).status_code == 200
    assert client.get(path, headers=auth).json()["description"] == "Đã chỉnh sửa"
    assert client.delete(path, headers=auth).status_code == 204
    assert client.get(path, headers=auth).status_code == 404
    # Dữ liệu nền vẫn tồn tại trong registry nhưng không xuất hiện lại trong API.
    assert all(
        entry["dataset"] != "demo_students" for entry in client.get(CATALOG, headers=auth).json()
    )
    assert client.post(CATALOG, json=original, headers=auth).status_code == 201


@pytest.mark.parametrize(
    "changes",
    [
        {"description": None},
        {"layer": "other"},
        {"dataset": "rename"},
        {"fields": [{"name": "id", "data_type": "string"}] * 2},
        {"fields": [{"name": " ", "data_type": "string"}]},
    ],
)
def test_invalid_patch_does_not_change_metadata(
    client: TestClient, changes: dict[str, object]
) -> None:
    auth = headers()
    before = client.get(f"{CATALOG}/demo/demo_students", headers=auth).json()
    response = client.patch(f"{CATALOG}/demo/demo_students", json=changes, headers=auth)
    assert response.status_code == 422
    assert client.get(f"{CATALOG}/demo/demo_students", headers=auth).json() == before


def test_unknown_domain_and_missing_dataset(client: TestClient) -> None:
    auth = headers()
    assert client.get(CATALOG, params={"domain_id": "missing"}, headers=auth).status_code == 404
    assert (
        client.post(CATALOG, json=PAYLOAD | {"domain_id": "missing"}, headers=auth).status_code
        == 404
    )
    assert client.get(DETAIL, headers=auth).status_code == 404
    assert client.patch(DETAIL, json={"description": "x"}, headers=auth).status_code == 404
    assert client.delete(DETAIL, headers=auth).status_code == 404


@pytest.mark.parametrize(
    "changes",
    [
        {"domain_id": "INVALID"},
        {"dataset": " "},
        {"schema_version": ""},
        {"layer": "invalid"},
        {"fields": [{"name": "id", "data_type": "string"}] * 2},
        {"unexpected": True},
    ],
)
def test_invalid_create(client: TestClient, changes: dict[str, object]) -> None:
    assert client.post(CATALOG, json=PAYLOAD | changes, headers=headers()).status_code == 422
    assert client.get(DETAIL, headers=headers()).status_code == 404


def test_empty_patch_preserves_metadata(client: TestClient) -> None:
    path = f"{CATALOG}/demo/demo_students"
    before = client.get(path, headers=headers()).json()
    response = client.patch(path, json={}, headers=headers())
    assert response.status_code == 200
    assert response.json() == before


def test_multiple_tables_persist_and_can_be_replaced(client: TestClient) -> None:
    payload = {key: value for key, value in PAYLOAD.items() if key != "fields"}
    tables = [
        {"name": "students", "description": "Sinh viên", "columns": PAYLOAD["fields"]},
        {
            "name": "classes",
            "description": "Lớp",
            "columns": [
                {"name": "class_id", "data_type": "integer", "description": "Mã lớp"},
            ],
        },
    ]
    created = client.post(CATALOG, json=payload | {"tables": tables}, headers=headers())
    assert created.status_code == 201
    entry = created.json()
    assert entry["fields"] == []
    assert [table["name"] for table in entry["tables"]] == ["students", "classes"]
    assert entry["tables"][1]["columns"][0]["data_type"] == "integer"
    assert client.get(DETAIL, headers=headers()).json() == entry
    assert entry in client.get(CATALOG, headers=headers()).json()
    assert client.patch(DETAIL, json={"fields": []}, headers=headers()).status_code == 422
    assert client.get(DETAIL, headers=headers()).json() == entry
    replaced = client.patch(DETAIL, json={"tables": [tables[1]]}, headers=headers())
    assert replaced.status_code == 200
    assert replaced.json()["fields"] == replaced.json()["tables"][0]["columns"]
    assert client.get(DETAIL, headers=headers()).json() == replaced.json()
    cleared = client.patch(DETAIL, json={"tables": []}, headers=headers())
    assert cleared.status_code == 200
    assert cleared.json()["tables"] == cleared.json()["fields"] == []


def test_legacy_fields_patch_updates_table_columns(client: TestClient) -> None:
    assert client.post(CATALOG, json=PAYLOAD, headers=headers()).status_code == 201
    response = client.patch(DETAIL, json={"fields": []}, headers=headers())
    assert response.status_code == 200
    assert response.json()["tables"][0]["columns"] == []
    assert client.get(DETAIL, headers=headers()).json() == response.json()


def test_old_database_payload_is_exposed_as_table(client: TestClient) -> None:
    path = f"{CATALOG}/demo/legacy_students"
    response = client.get(path, headers=headers())
    assert response.status_code == 200
    entry = response.json()
    assert entry["tables"] == [
        {
            "name": "legacy_students",
            "description": PAYLOAD["description"],
            "columns": entry["fields"],
        }
    ]
    updated = client.patch(path, json={"description": "Updated"}, headers=headers())
    assert updated.status_code == 200
    assert updated.json()["tables"] == entry["tables"]
    assert client.get(path, headers=headers()).json() == updated.json()


@pytest.mark.parametrize(
    "tables",
    [
        None,
        [{"name": " ", "columns": []}],
        [{"name": "students", "columns": []}] * 2,
        [{"name": "students", "columns": [{"name": "id", "data_type": "string"}] * 2}],
    ],
)
def test_invalid_tables_are_rejected(client: TestClient, tables: object) -> None:
    auth = headers()
    assert client.post(CATALOG, json=PAYLOAD | {"tables": tables}, headers=auth).status_code == 422
    assert client.post(CATALOG, json=PAYLOAD, headers=auth).status_code == 201
    before = client.get(DETAIL, headers=auth).json()
    assert client.patch(DETAIL, json={"tables": tables}, headers=auth).status_code == 422
    assert client.get(DETAIL, headers=auth).json() == before


def test_conflicting_fields_and_tables_are_rejected(client: TestClient) -> None:
    conflicting = {"tables": [{"name": "students", "columns": []}]}
    assert client.post(CATALOG, json=PAYLOAD | conflicting, headers=headers()).status_code == 422
    assert client.post(CATALOG, json=PAYLOAD, headers=headers()).status_code == 201
    response = client.patch(
        DETAIL, json=conflicting | {"fields": PAYLOAD["fields"]}, headers=headers()
    )
    assert response.status_code == 422


def test_openapi_documents_auth_and_crud(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    for path, methods in [
        (CATALOG, ["get", "post"]),
        (f"{CATALOG}/{{domain_id}}/{{dataset}}", ["get", "patch", "delete"]),
    ]:
        for method in methods:
            assert paths[path][method]["security"] == [{"HTTPBearer": []}]
