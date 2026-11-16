"""API chuông thông báo cho quản trị dữ liệu, manager và nhân viên."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.core.exceptions import AppException
from app.core.responses import COMMON_ERROR_RESPONSES
from app.core.roles import DATA_WRITE_ROLES, MANAGER_SCOPES, UPLOAD_STAFF_DOMAINS
from app.dependencies import get_db
from app.modules.notifications.schemas import MarkNotificationsRead, NotificationPage
from app.modules.staff.contracts import quality_issues
from app.repositories import notification_repo

router = APIRouter(
    prefix="/notifications", tags=["notifications"], responses=COMMON_ERROR_RESPONSES
)
User = Annotated[dict[str, Any], Depends(get_current_user)]


async def notification_db(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AsyncIterator[AsyncSession]:
    try:
        yield db
    except SQLAlchemyError:
        raise AppException(
            "Không thể đọc thông báo. Vui lòng thử lại sau.",
            code="NOTIFICATIONS_UNAVAILABLE",
            status_code=503,
        ) from None


DB = Annotated[AsyncSession, Depends(notification_db)]


async def current_actor(db: DB, user: User) -> quality_issues.IssueActor:
    return await quality_issues.issue_actor(db, user)


Actor = Annotated[quality_issues.IssueActor, Depends(current_actor)]


@router.get("", response_model=NotificationPage)
async def notifications(
    db: DB,
    actor: Actor,
    user: User,
    response: Response,
    workspace: Literal["ADMIN", "DATA_ADMIN", "MANAGER", "STAFF"] | None = None,
    unread_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> NotificationPage:
    response.headers["Cache-Control"] = "no-store"
    roles = set(user["roles"])
    if workspace is not None:
        allowed = (
            set(DATA_WRITE_ROLES)
            if workspace in ("ADMIN", "DATA_ADMIN")
            else set(MANAGER_SCOPES)
            if workspace == "MANAGER"
            else set(UPLOAD_STAFF_DOMAINS)
        )
        roles.intersection_update(allowed)
        if not roles:
            raise AppException(
                "Bạn không có quyền nhận thông báo cho nhóm vai trò này",
                code="FORBIDDEN",
                status_code=403,
            )
    return await notification_repo.list_notifications(
        db, actor, roles, unread_only=unread_only, limit=limit, offset=offset
    )


@router.post("/read", status_code=204)
async def mark_read(request: MarkNotificationsRead, db: DB, actor: Actor, user: User) -> Response:
    if any(event_id <= 0 for event_id in request.event_ids):
        raise AppException(
            "Mã thông báo không hợp lệ", code="INVALID_NOTIFICATION", status_code=422
        )
    if not await notification_repo.mark_read(db, actor, set(user["roles"]), set(request.event_ids)):
        raise AppException(
            "Không tìm thấy thông báo trong phạm vi được xem",
            code="NOTIFICATION_NOT_FOUND",
            status_code=404,
        )
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
