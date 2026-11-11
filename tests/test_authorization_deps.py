"""Kiểm thử xác thực và phân quyền tập trung tại get_current_user và refresh_token."""

from __future__ import annotations

import io
from collections.abc import Iterator
from datetime import timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Table, create_engine, delete, text, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateTable

from app.core.config import Settings
from app.core.deps import get_current_user, require_role
from app.core.exceptions import AppException
from app.core.roles import RoleCode
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
    hash_password,
)
from app.dependencies import get_db
from app.main import create_app
from app.modules.auth.models import LoginSession, Role, UserRole
from app.modules.auth.schemas import LoginRequest, LogoutRequest, RefreshTokenRequest
from app.modules.auth.service import AuthService
from app.modules.datasources.models import DataLayer, Dataset, DataSource, SourceSystem
from app.modules.ingestion.models import IngestionJob, IngestionWorkerHeartbeat
from app.modules.users.models import User


def _sqlite_auth_engine(*extra_tables: Table):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        execution_options={"schema_translate_map": {"app": None}},
    )
    tables = (
        cast("Table", Role.__table__),
        cast("Table", User.__table__),
        cast("Table", UserRole.__table__),
        cast("Table", LoginSession.__table__),
        *extra_tables,
    )
    with engine.begin() as conn:
        for table in tables:
            ddl = str(CreateTable(table).compile(conn))
            # SQLite chỉ auto-increment khóa chính INTEGER và không hỗ trợ schema app / cast ::jsonb.
            ddl = ddl.replace("app.", "")
            ddl = ddl.replace("BIGINT NOT NULL", "INTEGER NOT NULL")
            ddl = ddl.replace("'{}'::jsonb", "'{}'")
            ddl = ddl.replace("'[]'::jsonb", "'[]'")
            conn.execute(text(ddl))
    return engine


def _async_db_adapter(session: Session) -> MagicMock:
    db = MagicMock()
    db.execute = AsyncMock(side_effect=session.execute)
    db.scalar = AsyncMock(side_effect=session.scalar)
    db.scalars = AsyncMock(side_effect=session.scalars)
    db.get = AsyncMock(side_effect=session.get)
    db.add = session.add
    db.flush = AsyncMock(side_effect=session.flush)
    db.commit = AsyncMock(side_effect=session.commit)
    db.rollback = AsyncMock(side_effect=session.rollback)
    return db


def _seed_roles(session: Session) -> dict[str, int]:
    role_ids: dict[str, int] = {}
    for idx, role_code in enumerate(RoleCode, start=1):
        session.add(Role(role_id=idx, role_code=role_code.value, role_name=role_code.value))
        role_ids[role_code.value] = idx
    return role_ids


def test_jwt_access_token_expire_minutes_defaults_to_15_and_respects_env(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", raising=False)
    default_settings = Settings(JWT_SECRET_KEY="test-secret")
    assert default_settings.jwt_access_token_expire_minutes == 15

    monkeypatch.setenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "45")
    env_settings = Settings(JWT_SECRET_KEY="test-secret")
    assert env_settings.jwt_access_token_expire_minutes == 45


@pytest.mark.asyncio
@pytest.mark.parametrize("role_code", [role.value for role in RoleCode])
async def test_get_current_user_valid_token_active_user_unchanged_roles(role_code: str) -> None:
    engine = _sqlite_auth_engine()
    try:
        with Session(engine) as session, session.begin():
            role_ids = _seed_roles(session)
            session.add(
                User(
                    app_user_id=10,
                    username="active_user",
                    password_hash="hash",
                    is_active=True,
                )
            )
            session.add(UserRole(app_user_id=10, role_id=role_ids[role_code]))

        token = create_access_token("10", claims={"roles": [role_code]})
        with Session(engine) as session:
            db = _async_db_adapter(session)
            current_user = await get_current_user(f"Bearer {token}", db=db)

        assert current_user["sub"] == "10"
        assert current_user["roles"] == [role_code]
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_get_current_user_rejects_deactivated_user() -> None:
    engine = _sqlite_auth_engine()
    try:
        with Session(engine) as session, session.begin():
            role_ids = _seed_roles(session)
            session.add(
                User(
                    app_user_id=11,
                    username="locked_user",
                    password_hash="hash",
                    is_active=False,
                )
            )
            session.add(UserRole(app_user_id=11, role_id=role_ids[RoleCode.HR_STAFF.value]))

        token = create_access_token("11", claims={"roles": [RoleCode.HR_STAFF.value]})
        with Session(engine) as session:
            db = _async_db_adapter(session)
            with pytest.raises(AppException) as exc:
                await get_current_user(f"Bearer {token}", db=db)

        assert exc.value.status_code == 403
        assert exc.value.code == "USER_INACTIVE"
    finally:
        engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("token_roles", "db_roles", "expected_status", "expected_code"),
    [
        (["HR_STAFF"], [], 401, "TOKEN_STALE"),
        (["ADMIN", "HR_STAFF"], ["HR_STAFF"], 401, "TOKEN_STALE"),
        (["HR_STAFF"], ["HR_STAFF", "DATA_ADMIN"], 401, "TOKEN_STALE"),
        (["NON_EXISTENT_ROLE"], ["HR_STAFF"], 401, "TOKEN_STALE"),
        (["NON_EXISTENT_ROLE"], [], 401, "TOKEN_STALE"),
        ([], [], 403, "ROLE_REQUIRED"),
        ([], ["HR_STAFF"], 401, "TOKEN_STALE"),
    ],
)
async def test_get_current_user_role_mismatch_and_revocation(
    token_roles: list[str],
    db_roles: list[str],
    expected_status: int,
    expected_code: str,
) -> None:
    engine = _sqlite_auth_engine()
    try:
        with Session(engine) as session, session.begin():
            role_ids = _seed_roles(session)
            session.add(
                User(
                    app_user_id=12,
                    username="role_user",
                    password_hash="hash",
                    is_active=True,
                )
            )
            for role in db_roles:
                session.add(UserRole(app_user_id=12, role_id=role_ids[role]))

        token = create_access_token("12", claims={"roles": token_roles})
        with Session(engine) as session:
            db = _async_db_adapter(session)
            with pytest.raises(AppException) as exc:
                await get_current_user(f"Bearer {token}", db=db)

        assert exc.value.status_code == expected_status
        assert exc.value.code == expected_code
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_get_current_user_rejects_expired_invalid_and_missing_tokens() -> None:
    engine = _sqlite_auth_engine()
    try:
        with Session(engine) as session:
            db = _async_db_adapter(session)

            with pytest.raises(AppException) as missing_exc:
                await get_current_user(None, db=db)
            assert missing_exc.value.status_code == 401
            assert missing_exc.value.code == "UNAUTHORIZED"

            expired = create_access_token(
                "1", expires_delta=timedelta(minutes=-1), claims={"roles": ["ADMIN"]}
            )
            with pytest.raises(AppException) as expired_exc:
                await get_current_user(f"Bearer {expired}", db=db)
            assert expired_exc.value.status_code == 401
            assert expired_exc.value.code == "TOKEN_EXPIRED"

            with pytest.raises(AppException) as malformed_exc:
                await get_current_user("Bearer bad.token.value", db=db)
            assert malformed_exc.value.status_code == 401
            assert malformed_exc.value.code == "INVALID_TOKEN"

            refresh = create_refresh_token("1", claims={"roles": ["ADMIN"]})
            with pytest.raises(AppException) as refresh_exc:
                await get_current_user(f"Bearer {refresh}", db=db)
            assert refresh_exc.value.status_code == 401
            assert refresh_exc.value.code == "INVALID_TOKEN"

            for bad_sub in ("user-1", "0", "-3", ""):
                bad_sub_token = create_access_token(bad_sub, claims={"roles": ["ADMIN"]})
                with pytest.raises(AppException) as sub_exc:
                    await get_current_user(f"Bearer {bad_sub_token}", db=db)
                assert sub_exc.value.status_code == 401
                assert sub_exc.value.code == "INVALID_TOKEN"

            missing_user_token = create_access_token("999", claims={"roles": ["ADMIN"]})
            with pytest.raises(AppException) as missing_user_exc:
                await get_current_user(f"Bearer {missing_user_token}", db=db)
            assert missing_user_exc.value.status_code == 401
            assert missing_user_exc.value.code == "INVALID_TOKEN"

        valid_token = create_access_token("1", claims={"roles": ["ADMIN"]})
        with pytest.raises(AppException) as no_db_exc:
            await get_current_user(f"Bearer {valid_token}")
        assert no_db_exc.value.status_code == 503
        assert no_db_exc.value.code == "AUTH_UNAVAILABLE"
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_refresh_token_updates_roles_and_enforces_user_and_session_state() -> None:
    engine = _sqlite_auth_engine()
    try:
        with Session(engine) as session, session.begin():
            role_ids = _seed_roles(session)
            session.add(
                User(
                    app_user_id=20,
                    username="staff_user",
                    password_hash=hash_password("Secret@123"),
                    is_active=True,
                )
            )
            session.add(UserRole(app_user_id=20, role_id=role_ids[RoleCode.HR_STAFF.value]))

        with Session(engine) as session, session.begin():
            auth = AuthService(_async_db_adapter(session))
            logged_in = await auth.login(
                LoginRequest(username="staff_user", password="Secret@123"),
                ip_address="127.0.0.1",
                user_agent="pytest",
            )
            assert decode_access_token(logged_in.access_token)["roles"] == ["HR_STAFF"]

        # Đổi role trong DB từ HR_STAFF sang ACADEMIC_STAFF.
        with Session(engine) as session, session.begin():
            session.execute(delete(UserRole).where(UserRole.app_user_id == 20))
            session.add(UserRole(app_user_id=20, role_id=role_ids[RoleCode.ACADEMIC_STAFF.value]))

        # Access token cũ bị từ chối với 401 TOKEN_STALE.
        with Session(engine) as session:
            with pytest.raises(AppException) as stale_exc:
                await get_current_user(
                    f"Bearer {logged_in.access_token}", db=_async_db_adapter(session)
                )
            assert stale_exc.value.status_code == 401
            assert stale_exc.value.code == "TOKEN_STALE"

        # Refresh token hợp lệ cấp access token mới với role mới từ DB.
        with Session(engine) as session, session.begin():
            auth = AuthService(_async_db_adapter(session))
            refreshed = await auth.refresh_token(
                RefreshTokenRequest(refresh_token=logged_in.refresh_token)
            )
            assert decode_access_token(refreshed.access_token)["roles"] == ["ACADEMIC_STAFF"]

        # Thu hồi hết role -> refresh bị từ chối 403 ROLE_REQUIRED.
        with Session(engine) as session, session.begin():
            session.execute(delete(UserRole).where(UserRole.app_user_id == 20))

        with Session(engine) as session:
            auth = AuthService(_async_db_adapter(session))
            with pytest.raises(AppException) as no_role_exc:
                await auth.refresh_token(RefreshTokenRequest(refresh_token=refreshed.refresh_token))
            assert no_role_exc.value.status_code == 403
            assert no_role_exc.value.code == "ROLE_REQUIRED"

        # Khóa tài khoản -> refresh bị từ chối 403 USER_INACTIVE.
        with Session(engine) as session, session.begin():
            session.add(UserRole(app_user_id=20, role_id=role_ids[RoleCode.ACADEMIC_STAFF.value]))
            session.execute(update(User).where(User.app_user_id == 20).values(is_active=False))

        with Session(engine) as session:
            auth = AuthService(_async_db_adapter(session))
            with pytest.raises(AppException) as inactive_exc:
                await auth.refresh_token(RefreshTokenRequest(refresh_token=refreshed.refresh_token))
            assert inactive_exc.value.status_code == 403
            assert inactive_exc.value.code == "USER_INACTIVE"

        # Mở khóa nhưng logout (revoke session) -> refresh bị từ chối 401 SESSION_EXPIRED.
        with Session(engine) as session, session.begin():
            session.execute(update(User).where(User.app_user_id == 20).values(is_active=True))

        with Session(engine) as session, session.begin():
            auth = AuthService(_async_db_adapter(session))
            await auth.logout(LogoutRequest(refresh_token=refreshed.refresh_token))

        with Session(engine) as session:
            auth = AuthService(_async_db_adapter(session))
            with pytest.raises(AppException) as revoked_exc:
                await auth.refresh_token(RefreshTokenRequest(refresh_token=refreshed.refresh_token))
            assert revoked_exc.value.status_code == 401
            assert revoked_exc.value.code == "SESSION_EXPIRED"
    finally:
        engine.dispose()


def test_rbac_staff_domain_isolation_and_immediate_revocation_via_http() -> None:
    engine = _sqlite_auth_engine(
        cast("Table", DataLayer.__table__),
        cast("Table", SourceSystem.__table__),
        cast("Table", Dataset.__table__),
        cast("Table", DataSource.__table__),
        cast("Table", IngestionJob.__table__),
        cast("Table", IngestionWorkerHeartbeat.__table__),
    )
    try:
        with Session(engine) as session, session.begin():
            role_ids = _seed_roles(session)
            session.add(
                User(
                    app_user_id=1,
                    username="hr_staff",
                    password_hash="hash",
                    is_active=True,
                )
            )
            session.add(UserRole(app_user_id=1, role_id=role_ids[RoleCode.HR_STAFF.value]))
            session.add(
                User(
                    app_user_id=2,
                    username="admin_user",
                    password_hash="hash",
                    is_active=True,
                )
            )
            session.add(UserRole(app_user_id=2, role_id=role_ids[RoleCode.ADMIN.value]))
            session.add(
                User(
                    app_user_id=3,
                    username="data_admin_user",
                    password_hash="hash",
                    is_active=True,
                )
            )
            session.add(UserRole(app_user_id=3, role_id=role_ids[RoleCode.DATA_ADMIN.value]))

            session.add(DataLayer(data_layer_id=1, layer_code="BRONZE", layer_name="Bronze"))
            session.add(
                SourceSystem(
                    source_system_id=1,
                    source_code="UPLOAD_HR",
                    source_name="Upload HR",
                    source_type="UPLOAD",
                    is_active=True,
                )
            )
            session.add(
                SourceSystem(
                    source_system_id=2,
                    source_code="UPLOAD_ADM",
                    source_name="Upload Admissions",
                    source_type="UPLOAD",
                    is_active=True,
                )
            )
            session.add(
                Dataset(
                    dataset_id=10,
                    dataset_code="hr_employees",
                    dataset_name="HR Employees",
                    domain="hr",
                    data_layer_id=1,
                    source_system_id=1,
                    status="ACTIVE",
                )
            )
            session.add(
                Dataset(
                    dataset_id=11,
                    dataset_code="adm_applicants",
                    dataset_name="Admissions Applicants",
                    domain="admissions",
                    data_layer_id=1,
                    source_system_id=2,
                    status="ACTIVE",
                )
            )

        application = create_app()

        def override_db() -> Iterator[MagicMock]:
            with Session(engine) as session, session.begin():
                yield _async_db_adapter(session)

        application.dependency_overrides[get_db] = override_db

        hr_token = create_access_token("1", claims={"roles": ["HR_STAFF"]})
        admin_token = create_access_token("2", claims={"roles": ["ADMIN"]})
        data_admin_token = create_access_token("3", claims={"roles": ["DATA_ADMIN"]})

        with TestClient(application) as client:
            # 1. STAFF chỉ thấy dataset đúng domain của mình; bị ẩn domain khác.
            hr_headers = {"Authorization": f"Bearer {hr_token}"}
            datasets_resp = client.get("/api/v1/datasets", headers=hr_headers)
            assert datasets_resp.status_code == 200
            assert [item["domain"] for item in datasets_resp.json()] == ["hr"]
            assert client.get("/api/v1/datasets/10", headers=hr_headers).status_code == 200
            assert client.get("/api/v1/datasets/11", headers=hr_headers).status_code == 404

            # 2. STAFF không thể upload sang domain khác.
            upload_resp = client.post(
                "/api/v1/ingestion/uploads",
                headers=hr_headers,
                data={"dataset_name": "Admissions", "domain": "admissions"},
                files={"file": ("test.csv", io.BytesIO(b"a,b\n1,2\n"), "text/csv")},
            )
            assert upload_resp.status_code == 403

            # 3. STAFF không thể retry ingestion hoặc chạy cleaning.
            assert (
                client.post("/api/v1/ingestion/jobs/1/retry", headers=hr_headers).status_code == 403
            )
            assert (
                client.post(
                    "/api/v1/pipeline/cleaning/ingestion-jobs/1/preview",
                    headers=hr_headers,
                    json={"profile_id": "hr_employee"},
                ).status_code
                == 403
            )
            assert client.get("/api/v1/cleaning-runs", headers=hr_headers).status_code == 403

            # 4. ADMIN và DATA_ADMIN vẫn hoạt động đúng quyền.
            assert (
                client.get(
                    "/api/v1/ingestion/workers",
                    headers={"Authorization": f"Bearer {admin_token}"},
                ).status_code
                == 200
            )
            assert (
                client.get(
                    "/api/v1/ingestion/jobs",
                    headers={"Authorization": f"Bearer {data_admin_token}"},
                ).status_code
                == 200
            )

            # 5. Thu hồi role HR_STAFF trong DB -> token cũ bị từ chối ngay ở request tiếp theo.
            with Session(engine) as session, session.begin():
                session.execute(delete(UserRole).where(UserRole.app_user_id == 1))

            revoked_jobs = client.get("/api/v1/ingestion/jobs", headers=hr_headers)
            assert revoked_jobs.status_code == 401
            assert revoked_jobs.json()["error"]["code"] == "TOKEN_STALE"

            revoked_datasets = client.get("/api/v1/datasets", headers=hr_headers)
            assert revoked_datasets.status_code == 401
            assert revoked_datasets.json()["error"]["code"] == "TOKEN_STALE"

            revoked_upload = client.post(
                "/api/v1/ingestion/uploads",
                headers=hr_headers,
                data={"dataset_name": "HR New"},
                files={"file": ("test.csv", io.BytesIO(b"a,b\n1,2\n"), "text/csv")},
            )
            assert revoked_upload.status_code == 401
            assert revoked_upload.json()["error"]["code"] == "TOKEN_STALE"

            # 6. Cấp thêm quyền DATA_ADMIN cho user -> token HR_STAFF cũ không tự nhận quyền mới.
            with Session(engine) as session, session.begin():
                session.add(UserRole(app_user_id=1, role_id=role_ids[RoleCode.HR_STAFF.value]))
                session.add(UserRole(app_user_id=1, role_id=role_ids[RoleCode.DATA_ADMIN.value]))

            assert client.get("/api/v1/ingestion/workers", headers=hr_headers).status_code == 401
            assert (
                client.get("/api/v1/ingestion/workers", headers=hr_headers).json()["error"]["code"]
                == "TOKEN_STALE"
            )

            # 7. Khóa tài khoản DATA_ADMIN -> token bị từ chối 403 USER_INACTIVE ngay lập tức.
            with Session(engine) as session, session.begin():
                session.execute(update(User).where(User.app_user_id == 3).values(is_active=False))

            locked_resp = client.get(
                "/api/v1/ingestion/jobs",
                headers={"Authorization": f"Bearer {data_admin_token}"},
            )
            assert locked_resp.status_code == 403
            assert locked_resp.json()["error"]["code"] == "USER_INACTIVE"
    finally:
        engine.dispose()


def test_require_role_enforces_effective_roles() -> None:
    dependency = require_role(RoleCode.ADMIN, RoleCode.DATA_ADMIN)
    assert dependency({"sub": "1", "roles": ["DATA_ADMIN"]})["roles"] == ["DATA_ADMIN"]
    with pytest.raises(AppException) as exc:
        dependency({"sub": "1", "roles": ["HR_STAFF"]})
    assert exc.value.status_code == 403
    assert exc.value.code == "FORBIDDEN"
