"""Các dependency FastAPI dùng chung giữa các mô-đun."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.exceptions import AppException
from app.core.security import decode_access_token
from app.infra.db.session import get_db

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["get_current_user", "get_db", "require_role"]

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
    # Token hợp lệ sẽ trả về payload, trong đó "sub" chính là app_user_id.
    return decode_access_token(token)


def require_role(*roles: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Tạo dependency yêu cầu một trong các vai trò được cung cấp."""

    def dependency(
        current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    ) -> dict[str, Any]:
        user_roles = set(current_user.get("roles", []))
        if roles and user_roles.isdisjoint(roles):
            raise AppException("Insufficient permissions", code="FORBIDDEN", status_code=403)
        return current_user

    return dependency
