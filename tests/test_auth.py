"""Kiểm thử đơn vị cho dịch vụ xác thực và endpoint API đăng nhập."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import AppException
from app.core.security import create_refresh_token, hash_password
from app.infra.db.session import engine, get_db
from app.main import app
from app.modules.auth.schemas import (
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    RefreshTokenRequest,
    SessionRead,
    UserProfileDTO,
)
from app.modules.auth.service import AuthService


def _db() -> MagicMock:
    mock_db = MagicMock()
    mock_db.flush = AsyncMock()
    return mock_db


@pytest.fixture(autouse=True)
async def dispose_engine() -> AsyncGenerator[None]:
    """Giải phóng engine cơ sở dữ liệu trước mỗi kiểm thử để tránh lệch vòng lặp sự kiện."""
    await engine.dispose()
    yield
    await engine.dispose()


def _user(password: str = "Password@123", *, is_active: bool = True) -> MagicMock:
    mock_user = MagicMock()
    mock_user.app_user_id = 1
    mock_user.username = "testuser"
    mock_user.password_hash = hash_password(password)
    mock_user.is_active = is_active
    return mock_user


@pytest.mark.asyncio
async def test_login_success() -> None:
    """Kiểm tra đăng nhập thành công với tên người dùng và mật khẩu hợp lệ."""
    mock_db = _db()
    service = AuthService(db=mock_db)

    with (
        patch.object(service.user_repo, "get_by_username", new_callable=AsyncMock) as get_user,
        patch.object(service, "_get_role_codes", new_callable=AsyncMock) as get_roles,
    ):
        get_user.return_value = _user()
        get_roles.return_value = ["ADMIN"]

        response = await service.login(
            req=LoginRequest(username="testuser", password="Password@123"),
            ip_address="127.0.0.1",
            user_agent="pytest-client",
        )

    assert isinstance(response, LoginResponse)
    assert response.token_type == "bearer"
    assert response.access_token
    assert response.refresh_token
    assert response.user.username == "testuser"
    assert response.user.role == "ADMIN"
    mock_db.add.assert_called_once()
    mock_db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_login_user_not_found() -> None:
    """Kiểm tra dịch vụ đăng nhập trả lỗi 401 khi người dùng không tồn tại."""
    service = AuthService(db=_db())

    with patch.object(service.user_repo, "get_by_username", new_callable=AsyncMock) as get_user:
        get_user.return_value = None

        with pytest.raises(AppException) as exc_info:
            await service.login(
                req=LoginRequest(username="missing", password="Password@123"),
                ip_address=None,
                user_agent=None,
            )

    assert exc_info.value.status_code == 401
    assert exc_info.value.message == "Tên đăng nhập hoặc mật khẩu chưa đúng"


@pytest.mark.asyncio
async def test_login_invalid_password() -> None:
    """Kiểm tra dịch vụ đăng nhập trả lỗi 401 khi mật khẩu sai."""
    service = AuthService(db=_db())

    with patch.object(service.user_repo, "get_by_username", new_callable=AsyncMock) as get_user:
        get_user.return_value = _user(password="CorrectPassword123")

        with pytest.raises(AppException) as exc_info:
            await service.login(
                req=LoginRequest(username="testuser", password="WrongPassword123"),
                ip_address=None,
                user_agent=None,
            )

    assert exc_info.value.status_code == 401
    assert exc_info.value.message == "Tên đăng nhập hoặc mật khẩu chưa đúng"


@pytest.mark.asyncio
async def test_login_inactive_user() -> None:
    """Kiểm tra dịch vụ đăng nhập từ chối người dùng ngừng hoạt động sau khi xác minh mật khẩu."""
    service = AuthService(db=_db())

    with patch.object(service.user_repo, "get_by_username", new_callable=AsyncMock) as get_user:
        get_user.return_value = _user(is_active=False)

        with pytest.raises(AppException) as exc_info:
            await service.login(
                req=LoginRequest(username="testuser", password="Password@123"),
                ip_address=None,
                user_agent=None,
            )

    assert exc_info.value.status_code == 403
    assert exc_info.value.code == "USER_INACTIVE"


@pytest.mark.asyncio
async def test_refresh_token_success() -> None:
    """Kiểm tra luân chuyển refresh token trả về một cặp token mới."""
    mock_db = _db()
    service = AuthService(db=mock_db)
    old_refresh_token = create_refresh_token("1")
    session = MagicMock()

    with (
        patch.object(service, "_get_active_session", new_callable=AsyncMock) as get_session,
        patch.object(service.user_repo, "get", new_callable=AsyncMock) as get_user,
        patch.object(service, "_get_role_codes", new_callable=AsyncMock) as get_roles,
    ):
        get_session.return_value = session
        get_user.return_value = _user()
        get_roles.return_value = ["ADMIN"]

        response = await service.refresh_token(
            RefreshTokenRequest(refresh_token=old_refresh_token),
        )

    assert isinstance(response, LoginResponse)
    assert response.access_token
    assert response.refresh_token
    assert response.refresh_token != old_refresh_token
    assert response.user.role == "ADMIN"
    assert session.refresh_token_hash == service._hash_refresh_token(response.refresh_token)
    mock_db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_refresh_token_rejects_missing_session() -> None:
    """Kiểm tra refresh token bị từ chối khi không có phiên hoạt động tương ứng."""
    service = AuthService(db=_db())

    with patch.object(service, "_get_active_session", new_callable=AsyncMock) as get_session:
        get_session.return_value = None

        with pytest.raises(AppException) as exc_info:
            await service.refresh_token(
                RefreshTokenRequest(refresh_token=create_refresh_token("1")),
            )

    assert exc_info.value.status_code == 401
    assert exc_info.value.code == "SESSION_EXPIRED"


@pytest.mark.asyncio
async def test_refresh_token_rejects_access_token() -> None:
    """Kiểm tra endpoint làm mới token từ chối JWT không phải refresh token."""
    from app.core.security import create_access_token

    service = AuthService(db=_db())

    with pytest.raises(AppException) as exc_info:
        await service.refresh_token(
            RefreshTokenRequest(refresh_token=create_access_token("1")),
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.code == "INVALID_REFRESH_TOKEN"


@pytest.mark.asyncio
async def test_logout_success() -> None:
    """Kiểm tra đăng xuất thu hồi phiên đăng nhập đang hoạt động tương ứng."""
    mock_db = _db()
    service = AuthService(db=mock_db)
    refresh_token = create_refresh_token("1")
    session = MagicMock()

    with patch.object(service, "_get_active_session", new_callable=AsyncMock) as get_session:
        get_session.return_value = session

        result = await service.logout(LogoutRequest(refresh_token=refresh_token))

    assert result == {"message": "Bạn đã đăng xuất thành công"}
    assert session.session_status == "REVOKED"
    assert session.logout_at is not None
    mock_db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_logout_rejects_missing_token() -> None:
    """Kiểm tra đăng xuất từ chối yêu cầu rỗng không có refresh token."""
    service = AuthService(db=_db())

    with pytest.raises(AppException) as exc_info:
        await service.logout(LogoutRequest(refresh_token=None))

    assert exc_info.value.status_code == 401
    assert exc_info.value.code == "SESSION_EXPIRED"


@pytest.mark.asyncio
async def test_logout_rejects_missing_session() -> None:
    """Kiểm tra đăng xuất từ chối token không còn phiên hoạt động."""
    service = AuthService(db=_db())

    with patch.object(service, "_get_active_session", new_callable=AsyncMock) as get_session:
        get_session.return_value = None

        with pytest.raises(AppException) as exc_info:
            await service.logout(LogoutRequest(refresh_token=create_refresh_token("1")))

    assert exc_info.value.status_code == 401
    assert exc_info.value.code == "SESSION_EXPIRED"


@pytest.mark.asyncio
async def test_list_active_sessions_returns_current_user_sessions() -> None:
    """Kiểm tra list_active_sessions chỉ trả phiên đang hoạt động của người dùng hiện tại."""
    mock_db = _db()
    execute_result = MagicMock()
    first = MagicMock()
    first.login_session_id = 10
    first.login_at = datetime(2026, 10, 7, 9, 0, 0)
    first.ip_address = "127.0.0.1"
    first.user_agent = "pytest"
    first.session_status = "ACTIVE"
    execute_result.scalars.return_value.all.return_value = [first]
    mock_db.execute = AsyncMock(return_value=execute_result)
    service = AuthService(db=mock_db)

    sessions = await service.list_active_sessions(app_user_id=1)

    assert len(sessions) == 1
    assert sessions[0].login_session_id == 10
    assert sessions[0].session_status == "ACTIVE"
    mock_db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_revoke_session_revokes_owned_session() -> None:
    """Kiểm tra revoke_session thu hồi một phiên đang hoạt động thuộc người dùng."""
    mock_db = _db()
    execute_result = MagicMock()
    session = MagicMock()
    execute_result.scalar_one_or_none.return_value = session
    mock_db.execute = AsyncMock(return_value=execute_result)
    service = AuthService(db=mock_db)

    result = await service.revoke_session(session_id=10, app_user_id=1)

    assert result == {"message": "Đã đăng xuất thiết bị này"}
    assert session.session_status == "REVOKED"
    assert session.logout_at is not None
    mock_db.execute.assert_awaited_once()
    mock_db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_revoke_session_rejects_missing_or_foreign_session() -> None:
    """Kiểm tra revoke_session từ chối phiên không thuộc người dùng hoặc đã ngừng hoạt động."""
    mock_db = _db()
    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=execute_result)
    service = AuthService(db=mock_db)

    with pytest.raises(AppException) as exc_info:
        await service.revoke_session(session_id=10, app_user_id=1)

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "SESSION_NOT_FOUND"


@pytest.mark.asyncio
async def test_list_active_sessions_api_endpoint() -> None:
    """Kiểm thử tích hợp việc kết nối endpoint GET /api/v1/auth/sessions."""
    from httpx import ASGITransport, AsyncClient

    async def override_get_db() -> AsyncGenerator[MagicMock]:
        yield MagicMock()

    async def override_current_user() -> dict[str, object]:
        return {"sub": "1"}

    from app.core.deps import get_current_user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_current_user

    try:
        with patch.object(AuthService, "list_active_sessions", new_callable=AsyncMock) as list_mock:
            list_mock.return_value = [
                SessionRead(
                    login_session_id=10,
                    login_at=datetime(2026, 10, 7, 9, 0, 0),
                    ip_address="127.0.0.1",
                    user_agent="pytest",
                    session_status="ACTIVE",
                )
            ]

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.get(
                    "/api/v1/auth/sessions",
                    headers={"Authorization": "Bearer token"},
                )
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    assert response.json()[0]["login_session_id"] == 10
    list_mock.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_revoke_session_api_endpoint() -> None:
    """Kiểm thử tích hợp việc kết nối endpoint POST /api/v1/auth/revoke-session/{session_id}."""
    from httpx import ASGITransport, AsyncClient

    async def override_get_db() -> AsyncGenerator[MagicMock]:
        yield MagicMock()

    async def override_current_user() -> dict[str, object]:
        return {"sub": "1"}

    from app.core.deps import get_current_user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_current_user

    try:
        with patch.object(AuthService, "revoke_session", new_callable=AsyncMock) as revoke_mock:
            revoke_mock.return_value = {"message": "Đã đăng xuất thiết bị này"}

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post(
                    "/api/v1/auth/revoke-session/10",
                    headers={"Authorization": "Bearer token"},
                )
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    assert response.json() == {"message": "Đã đăng xuất thiết bị này"}
    revoke_mock.assert_awaited_once_with(session_id=10, app_user_id=1)


@pytest.mark.asyncio
async def test_remote_all_sessions_api_endpoint_removed() -> None:
    """Endpoint thu hồi hàng loạt cũ không còn thuộc luồng quản lý phiên người dùng."""
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            "/api/v1/auth/session",
            json={"app_user_id": 1},
        )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_legacy_remote_all_sessions_removed_from_service() -> None:
    """AuthService không còn cung cấp thao tác thu hồi hàng loạt không an toàn theo app_user_id."""
    assert not hasattr(AuthService, "remote_all_sessions")


@pytest.mark.asyncio
async def test_revoke_session_does_not_accept_app_user_id_body() -> None:
    """Luồng thu hồi giới hạn theo token người dùng hiện tại, không theo app_user_id trong thân yêu cầu."""
    mock_db = _db()
    execute_result = MagicMock()
    session = MagicMock()
    execute_result.scalar_one_or_none.return_value = session
    mock_db.execute = AsyncMock(return_value=execute_result)
    service = AuthService(db=mock_db)

    await service.revoke_session(session_id=10, app_user_id=1)

    mock_db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_revoke_session_rejects_invalid_current_user_token() -> None:
    """Hàm hỗ trợ router từ chối token không có subject dạng số."""
    from app.modules.auth.router import _current_user_id

    with pytest.raises(AppException) as exc_info:
        _current_user_id({"sub": "not-a-number"})

    assert exc_info.value.status_code == 401
    assert exc_info.value.code == "INVALID_TOKEN"


@pytest.mark.asyncio
async def test_session_query_uses_current_user_scope() -> None:
    """Thao tác liệt kê phiên nhận mã người dùng đã xác thực từ subject của token."""
    mock_db = _db()
    execute_result = MagicMock()
    empty_sessions: list[SessionRead] = []
    execute_result.scalars.return_value.all.return_value = empty_sessions
    mock_db.execute = AsyncMock(return_value=execute_result)
    service = AuthService(db=mock_db)

    sessions = await service.list_active_sessions(app_user_id=1)

    assert not sessions


@pytest.mark.asyncio
async def test_revoke_session_message() -> None:
    """Thao tác thu hồi phiên trả về thông báo thành công ổn định."""
    mock_db = _db()
    execute_result = MagicMock()
    session = MagicMock()
    execute_result.scalar_one_or_none.return_value = session
    mock_db.execute = AsyncMock(return_value=execute_result)
    service = AuthService(db=mock_db)

    result = await service.revoke_session(session_id=10, app_user_id=1)

    assert result["message"] == "Đã đăng xuất thiết bị này"


@pytest.mark.asyncio
async def test_no_bulk_revoke_request_schema() -> None:
    """Đã bỏ LogoutSessionRequest vì máy khách không còn truyền app_user_id."""
    import app.modules.auth.schemas as schemas

    assert not hasattr(schemas, "LogoutSessionRequest")


@pytest.mark.asyncio
async def test_active_session_read_shape() -> None:
    """Phản hồi phiên đang hoạt động chứa metadata thiết bị/phiên an toàn."""
    mock_db = _db()
    execute_result = MagicMock()
    session = MagicMock()
    session.login_session_id = 10
    session.login_at = datetime(2026, 10, 7, 9, 0, 0)
    session.ip_address = None
    session.user_agent = None
    session.session_status = "ACTIVE"
    execute_result.scalars.return_value.all.return_value = [session]
    mock_db.execute = AsyncMock(return_value=execute_result)
    service = AuthService(db=mock_db)

    result = await service.list_active_sessions(app_user_id=1)

    assert result[0].ip_address is None
    assert result[0].user_agent is None


@pytest.mark.asyncio
async def test_login_api_endpoint() -> None:
    """Kiểm thử tích hợp việc kết nối endpoint POST /api/v1/auth/login."""
    from httpx import ASGITransport, AsyncClient

    async def override_get_db() -> AsyncGenerator[MagicMock]:
        yield MagicMock()

    app.dependency_overrides[get_db] = override_get_db
    login_response = LoginResponse(
        access_token="access-token",
        refresh_token="refresh-token",
        user=UserProfileDTO(user_id=1, username="admin", role="ADMIN"),
    )

    try:
        with patch.object(AuthService, "login", new_callable=AsyncMock) as login_mock:
            login_mock.return_value = login_response

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post(
                    "/api/v1/auth/login",
                    json={"username": "admin", "password": "Password@123"},
                )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert response.json() == {
        "access_token": "access-token",
        "refresh_token": "refresh-token",
        "token_type": "bearer",
        "user": {"user_id": 1, "username": "admin", "role": "ADMIN"},
    }
    login_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_refresh_token_api_endpoint() -> None:
    """Kiểm thử tích hợp việc kết nối endpoint POST /api/v1/auth/refresh."""
    from httpx import ASGITransport, AsyncClient

    async def override_get_db() -> AsyncGenerator[MagicMock]:
        yield MagicMock()

    app.dependency_overrides[get_db] = override_get_db
    refresh_response = LoginResponse(
        access_token="new-access-token",
        refresh_token="new-refresh-token",
        user=UserProfileDTO(user_id=1, username="admin", role="ADMIN"),
    )

    try:
        with patch.object(AuthService, "refresh_token", new_callable=AsyncMock) as refresh_mock:
            refresh_mock.return_value = refresh_response

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post(
                    "/api/v1/auth/refresh",
                    json={"refresh_token": "old-refresh-token"},
                )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert response.json() == {
        "access_token": "new-access-token",
        "refresh_token": "new-refresh-token",
        "token_type": "bearer",
        "user": {"user_id": 1, "username": "admin", "role": "ADMIN"},
    }
    refresh_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_logout_api_endpoint() -> None:
    """Kiểm thử tích hợp việc kết nối endpoint POST /api/v1/auth/logout."""
    from httpx import ASGITransport, AsyncClient

    async def override_get_db() -> AsyncGenerator[MagicMock]:
        yield MagicMock()

    app.dependency_overrides[get_db] = override_get_db

    try:
        with patch.object(AuthService, "logout", new_callable=AsyncMock) as logout_mock:
            logout_mock.return_value = {"message": "Bạn đã đăng xuất thành công"}

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post(
                    "/api/v1/auth/logout",
                    json={"refresh_token": "refresh-token"},
                )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert response.json() == {"message": "Bạn đã đăng xuất thành công"}
    logout_mock.assert_awaited_once()
