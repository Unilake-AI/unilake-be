"""Shared manager API dependencies, independent of router registration."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_role
from app.core.exceptions import AppException
from app.core.roles import MANAGER_SCOPES
from app.dependencies import get_db
from app.modules.manager.access import ManagerAccess, manager_access

User = Annotated[dict[str, Any], Depends(require_role(*MANAGER_SCOPES))]


async def manager_db(db: Annotated[AsyncSession, Depends(get_db)]) -> AsyncIterator[AsyncSession]:
    try:
        yield db
    except SQLAlchemyError:
        raise AppException(
            "Không thể đọc báo cáo. Kiểm tra kết nối DB và migration Gold.",
            code="MANAGER_UNAVAILABLE",
            status_code=503,
        ) from None


DB = Annotated[AsyncSession, Depends(manager_db)]


async def current_manager(db: DB, user: User) -> ManagerAccess:
    return await manager_access(db, user)


Manager = Annotated[ManagerAccess, Depends(current_manager)]
