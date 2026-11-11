"""Các dependency FastAPI dùng chung giữa các mô-đun."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.roles import RoleCode
from app.core.security import decode_access_token
from app.dependencies import get_db
from app.repositories.department_access_repo import user_auth_state

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["get_current_user", "require_role"]

bearer_scheme = HTTPBearer(auto_error=False)


def _parse_user_id(subject: Any) -> int:
    if isinstance(subject, bool) or not isinstance(subject, str | int):
        raise AppException(
            "Phiên đăng nhập không hợp lệ. Vui lòng đăng nhập lại.",
            code="INVALID_TOKEN",
            status_code=401,
        )
    if isinstance(subject, str):
        stripped = subject.strip()
        if not stripped.isdigit():
            raise AppException(
                "Phiên đăng nhập không hợp lệ. Vui lòng đăng nhập lại.",
                code="INVALID_TOKEN",
                status_code=401,
            )
        user_id = int(stripped)
    else:
        user_id = subject
    if user_id <= 0:
        raise AppException(
            "Phiên đăng nhập không hợp lệ. Vui lòng đăng nhập lại.",
            code="INVALID_TOKEN",
            status_code=401,
        )
    return user_id


async def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | str | None,
        Depends(bearer_scheme),
    ] = None,
    db: Annotated[AsyncSession | None, Depends(get_db)] = None,
) -> dict[str, Any]:
    """Xác định người dùng hiện tại từ bearer token và đối chiếu trạng thái/vai trò trong DB."""
    # Swagger Authorize và client thật đều gửi access token qua header Authorization.
    # HTTPBearer tự tách phần "Bearer", nên phía endpoint không cần nhận header thủ công.
    token: str | None = None
    if isinstance(credentials, HTTPAuthorizationCredentials):
        token = credentials.credentials
    elif isinstance(credentials, str):
        # Nhánh này giữ cho unit test cũ có thể gọi trực tiếp bằng chuỗi Bearer token.
        token = credentials.removeprefix("Bearer ").removeprefix("bearer ").strip()

    if not token:
        raise AppException(
            "Vui lòng đăng nhập để tiếp tục",
            code="UNAUTHORIZED",
            status_code=401,
        )

    payload = decode_access_token(token)
    if payload.get("type") == "refresh":
        raise AppException(
            "Phiên đăng nhập không hợp lệ. Vui lòng đăng nhập lại.",
            code="INVALID_TOKEN",
            status_code=401,
        )

    user_id = _parse_user_id(payload.get("sub"))

    raw_roles = payload.get("roles")
    if raw_roles is not None and (
        not isinstance(raw_roles, list) or not all(isinstance(role, str) for role in raw_roles)
    ):
        raise AppException(
            "Phiên đăng nhập không hợp lệ. Vui lòng đăng nhập lại.",
            code="INVALID_TOKEN",
            status_code=401,
        )
    token_roles = set(raw_roles or [])

    if db is None:
        raise AppException(
            "Không thể xác thực quyền truy cập",
            code="AUTH_UNAVAILABLE",
            status_code=503,
        )

    try:
        auth_state = await user_auth_state(db, user_id)
    except SQLAlchemyError:
        raise AppException(
            "Không thể xác thực quyền truy cập",
            code="AUTH_UNAVAILABLE",
            status_code=503,
        ) from None

    if auth_state is None:
        raise AppException(
            "Phiên đăng nhập không hợp lệ. Vui lòng đăng nhập lại.",
            code="INVALID_TOKEN",
            status_code=401,
        )

    is_active, raw_db_roles = auth_state
    if not is_active:
        raise AppException(
            "Tài khoản của bạn đang bị khóa, vui lòng liên hệ quản trị viên",
            code="USER_INACTIVE",
            status_code=403,
        )

    # Chỉ giữ các vai trò được hệ thống hỗ trợ và đối chiếu chính xác với DB.
    supported_roles = {role.value for role in RoleCode}
    db_roles = {role for role in raw_db_roles if role in supported_roles}
    if not db_roles and not token_roles:
        raise AppException(
            "Tài khoản chưa được gán vai trò hợp lệ. Vui lòng liên hệ quản trị viên.",
            code="ROLE_REQUIRED",
            status_code=403,
        )
    if token_roles != db_roles:
        raise AppException(
            "Quyền truy cập đã thay đổi. Vui lòng làm mới phiên đăng nhập.",
            code="TOKEN_STALE",
            status_code=401,
        )
    return {**payload, "roles": sorted(db_roles)}


def require_role(*roles: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Tạo dependency yêu cầu một trong các vai trò được cung cấp."""

    def dependency(
        current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    ) -> dict[str, Any]:
        user_roles = set(current_user.get("roles", []))
        if roles and user_roles.isdisjoint(roles):
            raise AppException(
                "Bạn không có quyền thực hiện thao tác này. Vui lòng liên hệ quản trị viên nếu cần hỗ trợ.",
                code="FORBIDDEN",
                status_code=403,
            )
        return current_user

    return dependency
