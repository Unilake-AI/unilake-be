"""Các dependency FastAPI dùng chung giữa các mô-đun."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.exceptions import AppException
from app.core.roles import RoleCode
from app.core.security import decode_access_token

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["get_current_user", "require_role"]

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | str | None,
        Depends(bearer_scheme),
    ] = None,
) -> dict[str, Any]:
    """Xác định người dùng hiện tại từ bearer token."""
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
    # Chỉ giữ vai trò hiện hành, kể cả với token đã cấp trước khi cập nhật.
    payload = decode_access_token(token)
    raw_roles = payload.get("roles")
    # So sánh với mã chuỗi vì Python 3.11 chưa hỗ trợ kiểm tra chuỗi trực tiếp trong Enum.
    supported_roles = {role.value for role in RoleCode}
    roles = (
        [role for role in raw_roles if isinstance(role, str) and role in supported_roles]
        if isinstance(raw_roles, list)
        else []
    )
    if not roles:
        raise AppException(
            "Tài khoản chưa được gán vai trò hợp lệ. Vui lòng liên hệ quản trị viên.",
            code="ROLE_REQUIRED",
            status_code=403,
        )
    return {**payload, "roles": roles}


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
