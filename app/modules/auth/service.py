"""Logic dịch vụ xác thực."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
    verify_password,
)
from app.modules.auth.models import LoginSession, Role, UserRole
from app.modules.auth.schemas import LoginResponse, SessionRead, UserProfileDTO
from app.repositories.user_repo import UserRepository

if TYPE_CHECKING:
    from app.modules.auth.schemas import LoginRequest, LogoutRequest, RefreshTokenRequest


class AuthService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.user_repo = UserRepository(db)

    async def login(
        self,
        req: LoginRequest,
        ip_address: str | None,
        user_agent: str | None,
    ) -> LoginResponse:
        # Tìm tài khoản theo username người dùng nhập.
        exist_user = await self.user_repo.get_by_username(req.username)

        if not exist_user:
            raise AppException(
                message="Tên đăng nhập hoặc mật khẩu chưa đúng",
                status_code=401,
                code="UNAUTHORIZED",
            )

        # Kiểm tra mật khẩu nhưng không nói rõ sai username hay password.
        password_match = verify_password(req.password, exist_user.password_hash)

        if not password_match:
            raise AppException(
                message="Tên đăng nhập hoặc mật khẩu chưa đúng",
                status_code=401,
                code="UNAUTHORIZED",
            )
        if not exist_user.is_active:
            raise AppException(
                message="Tài khoản của bạn đang bị khóa, vui lòng liên hệ quản trị viên",
                status_code=403,
                code="USER_INACTIVE",
            )

        # Lấy quyền, tạo cặp token và lưu phiên đăng nhập hiện tại.
        role_codes = await self._get_role_codes(exist_user.app_user_id)
        access_token = create_access_token(
            str(exist_user.app_user_id),
            claims={"username": exist_user.username, "roles": role_codes},
        )
        refresh_token = create_refresh_token(str(exist_user.app_user_id))
        self.db.add(
            LoginSession(
                app_user_id=exist_user.app_user_id,
                ip_address=ip_address,
                user_agent=user_agent,
                refresh_token_hash=self._hash_refresh_token(refresh_token),
            )
        )
        await self.db.flush()

        return LoginResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            user=UserProfileDTO(
                user_id=exist_user.app_user_id,
                username=exist_user.username,
                role=role_codes[0] if role_codes else "USER",
            ),
        )

    # Hàm lấy danh sách quyền của user từ AppUserRole và Role
    async def _get_role_codes(self, app_user_id: int) -> list[str]:
        # Gom tất cả role của user để đưa vào access token.
        query = (
            select(Role.role_code)
            .join(UserRole, UserRole.role_id == Role.role_id)
            .where(UserRole.app_user_id == app_user_id)
            .order_by(Role.role_code)
        )
        result = await self.db.execute(query)
        return list(result.scalars().all())

    @staticmethod
    def _hash_refresh_token(refresh_token: str) -> str:
        # Không lưu refresh token gốc trong DB; chỉ lưu hash để giảm rủi ro lộ phiên.
        return hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()

    async def refresh_token(self, req: RefreshTokenRequest) -> LoginResponse:
        # Kiểm tra refresh token có hợp lệ và đúng loại dùng để gia hạn phiên.
        payload = decode_access_token(req.refresh_token)
        if payload.get("type") != "refresh":
            raise AppException(
                message="Phiên đăng nhập không hợp lệ, vui lòng đăng nhập lại",
                status_code=401,
                code="INVALID_REFRESH_TOKEN",
            )
        try:
            app_user_id = int(payload["sub"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AppException(
                message="Phiên đăng nhập không hợp lệ, vui lòng đăng nhập lại",
                status_code=401,
                code="INVALID_REFRESH_TOKEN",
            ) from exc

        # Bảo đảm phiên này vẫn còn ACTIVE trong DB.
        session = await self._get_active_session(req.refresh_token, app_user_id)
        if session is None:
            raise AppException(
                message="Phiên đăng nhập đã hết hạn, vui lòng đăng nhập lại",
                status_code=401,
                code="SESSION_EXPIRED",
            )

        # Kiểm tra user còn tồn tại và chưa bị khóa.
        user = await self.user_repo.get(app_user_id)
        if user is None:
            raise AppException(
                message="Phiên đăng nhập không hợp lệ, vui lòng đăng nhập lại",
                status_code=401,
                code="INVALID_REFRESH_TOKEN",
            )
        if not user.is_active:
            raise AppException(
                message="Tài khoản của bạn đang bị khóa, vui lòng liên hệ quản trị viên",
                status_code=403,
                code="USER_INACTIVE",
            )

        # Cấp access token mới và rotate refresh token để phiên cũ không dùng lại.
        role_codes = await self._get_role_codes(user.app_user_id)
        access_token = create_access_token(
            str(user.app_user_id),
            claims={"username": user.username, "roles": role_codes},
        )
        refresh_token = create_refresh_token(str(user.app_user_id))
        session.refresh_token_hash = self._hash_refresh_token(refresh_token)
        await self.db.flush()

        return LoginResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            user=UserProfileDTO(
                user_id=user.app_user_id,
                username=user.username,
                role=role_codes[0] if role_codes else "USER",
            ),
        )

    # Hàm tìm session active của user
    async def _get_active_session(
        self,
        refresh_token: str,
        app_user_id: int,
    ) -> LoginSession | None:
        # Tìm đúng phiên ACTIVE bằng user id và hash của refresh token.
        query = select(LoginSession).where(
            LoginSession.app_user_id == app_user_id,
            LoginSession.refresh_token_hash == self._hash_refresh_token(refresh_token),
            LoginSession.session_status == "ACTIVE",
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def logout(self, req: LogoutRequest) -> dict[str, str]:
        # Logout cần refresh token để biết chính xác phiên nào đang đăng xuất.
        if not req.refresh_token:
            raise AppException(
                message="Phiên đăng nhập đã hết hạn, vui lòng đăng nhập lại",
                status_code=401,
                code="SESSION_EXPIRED",
            )

        # Kiểm tra refresh token và lấy user id từ token.
        payload = decode_access_token(req.refresh_token)
        if payload.get("type") != "refresh":
            raise AppException(
                message="Phiên đăng nhập không hợp lệ, vui lòng đăng nhập lại",
                status_code=401,
                code="INVALID_REFRESH_TOKEN",
            )
        try:
            app_user_id = int(payload["sub"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AppException(
                message="Phiên đăng nhập không hợp lệ, vui lòng đăng nhập lại",
                status_code=401,
                code="INVALID_REFRESH_TOKEN",
            ) from exc

        # Tìm phiên active tương ứng, rồi đánh dấu đã đăng xuất.
        session = await self._get_active_session(req.refresh_token, app_user_id)
        if session is None:
            raise AppException(
                message="Phiên đăng nhập đã hết hạn, vui lòng đăng nhập lại",
                status_code=401,
                code="SESSION_EXPIRED",
            )

        session.session_status = "REVOKED"
        session.logout_at = datetime.now()
        await self.db.flush()
        return {"message": "Bạn đã đăng xuất thành công"}

    async def list_active_sessions(self, app_user_id: int) -> list[SessionRead]:
        """Trả về các phiên đăng nhập đang hoạt động thuộc người dùng hiện tại."""
        # Chỉ liệt kê phiên ACTIVE của chính user đang đăng nhập.
        query = (
            select(LoginSession)
            .where(
                LoginSession.app_user_id == app_user_id,
                LoginSession.session_status == "ACTIVE",
            )
            .order_by(LoginSession.login_at.desc())
        )
        result = await self.db.execute(query)
        sessions = result.scalars().all()
        # Chỉ trả metadata đủ để user nhận diện thiết bị, không trả refresh token/hash.
        return [
            SessionRead(
                login_session_id=session.login_session_id,
                login_at=session.login_at,
                ip_address=session.ip_address,
                user_agent=session.user_agent,
                session_status=session.session_status,
            )
            for session in sessions
        ]

    async def revoke_session(self, session_id: int, app_user_id: int) -> dict[str, str]:
        """Thu hồi một phiên đang hoạt động thuộc người dùng hiện tại."""
        # Tìm phiên theo session_id nhưng luôn khóa trong phạm vi current user.
        query = select(LoginSession).where(
            LoginSession.login_session_id == session_id,
            LoginSession.app_user_id == app_user_id,
            LoginSession.session_status == "ACTIVE",
        )
        result = await self.db.execute(query)
        session = result.scalar_one_or_none()
        if session is None:
            # Không tìm thấy nghĩa là session không tồn tại, đã logout, hoặc không thuộc user này.
            raise AppException(
                message="Không tìm thấy phiên đăng nhập này hoặc phiên đã được đăng xuất",
                status_code=404,
                code="SESSION_NOT_FOUND",
            )
        # Revoke phiên được chọn để lần refresh tiếp theo trên thiết bị đó bị từ chối.
        session.session_status = "REVOKED"
        session.logout_at = datetime.now()
        await self.db.flush()
        return {"message": "Đã đăng xuất thiết bị này"}
