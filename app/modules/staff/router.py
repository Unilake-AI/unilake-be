"""Nhận yêu cầu lấy quyền và lựa chọn cho màn hình nhân viên."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.dependencies import get_db
from app.modules.staff.schemas import StaffContext
from app.modules.staff.services import context

router = APIRouter(prefix="/staff", tags=["staff"])


@router.get("/context", response_model=StaffContext, summary="Quyền và lựa chọn cho màn hình STAFF")
async def staff_context(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> StaffContext:
    return await context.get_context(db, user)
