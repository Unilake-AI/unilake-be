"""Các schema Pydantic cho API xác thực."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class LoginRequest(BaseModel):
    """Thông tin xác thực trong yêu cầu đăng nhập."""

    username: str
    password: str


class UserProfileDTO(BaseModel):
    """Hồ sơ người dùng đã xác thực được trả về cho máy khách."""

    user_id: int
    username: str
    role: str


# Hàm trả về access_token và refresh_token
class LoginResponse(BaseModel):
    """Phản hồi đăng nhập kèm hồ sơ người dùng."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserProfileDTO


class RefreshTokenRequest(BaseModel):
    """Dữ liệu yêu cầu cấp access token mới bằng refresh token."""

    refresh_token: str


class LogoutRequest(BaseModel):
    """Dữ liệu yêu cầu đăng xuất."""

    refresh_token: str | None = None


class MessageResponse(BaseModel):
    """Phản hồi trạng thái/thông báo dùng chung."""

    message: str


class SessionRead(BaseModel):
    """Phiên đăng nhập đang hoạt động được trả về cho người dùng đã xác thực."""

    login_session_id: int
    login_at: datetime
    ip_address: str | None = None
    user_agent: str | None = None
    session_status: str
