"""Kiểm tra vai trò hiện hành và kết nối đọc báo cáo."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_role
from app.core.exceptions import AppException
from app.core.roles import RoleCode
from app.dependencies import get_db
from app.modules.viewer.access import viewer_access
from app.modules.viewer.schemas import ViewerContext

User = Annotated[dict[str, Any], Depends(require_role(RoleCode.VIEWER))]


def current_viewer(user: User) -> ViewerContext:
    return viewer_access(user)


Viewer = Annotated[ViewerContext, Depends(current_viewer)]


async def viewer_db(db: Annotated[AsyncSession, Depends(get_db)]) -> AsyncIterator[AsyncSession]:
    try:
        yield db
    except SQLAlchemyError:
        raise AppException(
            "Không thể đọc báo cáo toàn trường",
            code="VIEWER_UNAVAILABLE",
            status_code=503,
        ) from None


DB = Annotated[AsyncSession, Depends(viewer_db)]
