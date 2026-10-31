"""Các tuyến API xác thực."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.core.exceptions import AppException
from app.infra.db.session import get_db
from app.modules.auth.schemas import (
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    MessageResponse,
    RefreshTokenRequest,
    SessionRead,
)
from app.modules.auth.service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


def _current_user_id(current_user: dict[str, object]) -> int:
    # Lấy user id từ access token đã được get_current_user xác thực.
    subject = current_user.get("sub")
    if not isinstance(subject, str | int):
        raise AppException(
            message="Phiên đăng nhập không hợp lệ, vui lòng đăng nhập lại",
            status_code=401,
            code="INVALID_TOKEN",
        )
    try:
        return int(subject)
    except ValueError as exc:
        raise AppException(
            message="Phiên đăng nhập không hợp lệ, vui lòng đăng nhập lại",
            status_code=401,
            code="INVALID_TOKEN",
        ) from exc


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    # Lưu thêm IP và user-agent để người dùng nhận diện thiết bị trong danh sách phiên.
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    service = AuthService(db=db)
    return await service.login(
        req=payload,
        ip_address=client_ip,
        user_agent=user_agent,
    )


@router.post("/refresh", response_model=LoginResponse)
async def refresh_token(
    payload: RefreshTokenRequest,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    # Client gửi refresh_token trong body để lấy access_token mới khi access token hết hạn.
    service = AuthService(db=db)
    return await service.refresh_token(payload)


@router.post("/logout", response_model=MessageResponse)
async def logout(
    payload: LogoutRequest,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    # Logout phiên hiện tại dựa trên refresh_token của phiên đó.
    service = AuthService(db=db)
    result = await service.logout(payload)
    return MessageResponse(**result)


@router.get("/sessions", response_model=list[SessionRead])
async def list_active_sessions(
    db: AsyncSession = Depends(get_db),
    current_user: dict[str, object] = Depends(get_current_user),
) -> list[SessionRead]:
    # Không nhận app_user_id từ client; user id được lấy từ access token đã xác thực.
    service = AuthService(db=db)
    return await service.list_active_sessions(_current_user_id(current_user))


@router.post("/revoke-session/{session_id}", response_model=MessageResponse)
async def revoke_session(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict[str, object] = Depends(get_current_user),
) -> MessageResponse:
    # Chỉ revoke session thuộc về current user, tránh người dùng đá phiên của tài khoản khác.
    service = AuthService(db=db)
    result = await service.revoke_session(
        session_id=session_id,
        app_user_id=_current_user_id(current_user),
    )
    return MessageResponse(**result)
