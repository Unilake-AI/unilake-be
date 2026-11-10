"""Trả các lựa chọn và quyền hiện có để giao diện không yêu cầu người dùng gõ mã nội bộ."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_current_user
from app.core.exceptions import AppException
from app.core.roles import DATA_WRITE_ROLES, UPLOAD_STAFF_DOMAINS
from app.dependencies import get_db
from app.repositories.department_access_repo import (
    account_username,
    active_department_choices,
    active_roles,
)

router = APIRouter(prefix="/staff", tags=["staff"])
DOMAIN_NAMES = {"hr": "Nhân sự", "academic": "Đào tạo", "admissions": "Tuyển sinh"}


class DomainChoice(BaseModel):
    domain: str
    name: str


class DepartmentChoice(BaseModel):
    department_id: int
    department_code: str
    department_name: str


class StaffContext(BaseModel):
    user_id: int
    username: str
    roles: list[str]
    domains: list[DomainChoice]
    default_domain: str | None
    departments: list[DepartmentChoice]
    can_upload: bool
    can_resolve_issues: bool
    can_manage_silver: bool
    needs_department_assignment: bool
    accepted_extensions: list[str]
    max_file_bytes: int
    max_records: int
    note: str


@router.get("/context", response_model=StaffContext, summary="Quyền và lựa chọn cho màn hình STAFF")
async def staff_context(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> StaffContext:
    try:
        user_id = int(user["sub"])
    except (KeyError, TypeError, ValueError):
        raise AppException(
            "Phiên đăng nhập không hợp lệ", code="INVALID_TOKEN", status_code=401
        ) from None
    try:
        roles = await active_roles(db, user_id)
        roles.intersection_update(user.get("roles", []))
        is_admin = not DATA_WRITE_ROLES.isdisjoint(roles)
        domains = {domain for role, domain in UPLOAD_STAFF_DOMAINS.items() if role in roles}
        if not is_admin and not domains:
            raise AppException(
                "Bạn không có quyền sử dụng màn hình STAFF", code="FORBIDDEN", status_code=403
            )
        if is_admin:
            domains = set(DOMAIN_NAMES)
        departments = await active_department_choices(db, user_id)
        username = await account_username(db, user_id)
    except SQLAlchemyError:
        raise AppException(
            "Không thể đọc quyền tài khoản", code="STAFF_CONTEXT_UNAVAILABLE", status_code=503
        ) from None
    return StaffContext(
        user_id=user_id,
        username=str(username),
        roles=sorted(roles),
        domains=[
            DomainChoice(domain=domain, name=DOMAIN_NAMES[domain]) for domain in sorted(domains)
        ],
        default_domain=next(iter(domains)) if len(domains) == 1 else None,
        departments=[DepartmentChoice(**row) for row in departments],
        can_upload=True,
        can_resolve_issues=bool(
            departments and any(role in roles for role in UPLOAD_STAFF_DOMAINS)
        ),
        can_manage_silver=is_admin,
        needs_department_assignment=not is_admin and not departments,
        accepted_extensions=[".csv", ".xlsx", ".xlsm"],
        max_file_bytes=settings.upload_max_bytes,
        max_records=settings.upload_max_records,
        note="Cần phân công phòng ban đang hiệu lực để nhận và báo sửa dữ liệu"
        if not departments
        else "Chỉ người được giao mới được báo đã sửa",
    )
