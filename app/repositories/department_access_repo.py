"""Dùng cùng thông tin phân công phòng ban cho màn hình STAFF và yêu cầu sửa dữ liệu."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select

from app.modules.auth.models import Role, UserRole
from app.modules.users.models import Assignment, Department, Employee, User

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.sql.elements import ColumnElement


def current_assignment_conditions() -> tuple[ColumnElement[bool], ...]:
    today = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date()
    return (
        User.is_active.is_(True),
        func.upper(Employee.employee_status) == "ACTIVE",
        func.upper(Assignment.assignment_status) == "ACTIVE",
        Department.status == "ACTIVE",
        Assignment.start_date <= today,
        or_(Assignment.end_date.is_(None), Assignment.end_date >= today),
    )


async def user_auth_state(db: AsyncSession, user_id: int) -> tuple[bool, set[str]] | None:
    """Trả về trạng thái hoạt động và tập vai trò hiện hành của người dùng trong DB."""
    rows = (
        await db.execute(
            select(User.is_active, Role.role_code)
            .outerjoin(UserRole, UserRole.app_user_id == User.app_user_id)
            .outerjoin(Role, Role.role_id == UserRole.role_id)
            .where(User.app_user_id == user_id)
        )
    ).all()
    if not rows:
        return None
    is_active = bool(rows[0][0])
    roles = {role_code for _, role_code in rows if isinstance(role_code, str)}
    return is_active, roles


async def active_roles(db: AsyncSession, user_id: int) -> set[str]:
    return set(
        await db.scalars(
            select(Role.role_code)
            .join(UserRole, UserRole.role_id == Role.role_id)
            .join(User, User.app_user_id == UserRole.app_user_id)
            .where(User.app_user_id == user_id, User.is_active.is_(True))
        )
    )


async def active_department_choices(db: AsyncSession, user_id: int) -> list[dict[str, Any]]:
    rows = await db.execute(
        select(Department.department_id, Department.department_code, Department.department_name)
        .select_from(User)
        .join(Employee, Employee.employee_id == User.employee_id)
        .join(Assignment, Assignment.employee_id == Employee.employee_id)
        .join(Department, Department.department_id == Assignment.department_id)
        .where(User.app_user_id == user_id, *current_assignment_conditions())
        .distinct()
        .order_by(Department.department_id)
    )
    return [dict(row) for row in rows.mappings()]


async def account_username(db: AsyncSession, user_id: int) -> str:
    return str(await db.scalar(select(User.username).where(User.app_user_id == user_id)))


async def active_departments(db: AsyncSession) -> list[dict[str, Any]]:
    rows = await db.execute(
        select(Department.department_id, Department.department_code, Department.department_name)
        .where(Department.status == "ACTIVE")
        .order_by(Department.department_name)
    )
    return [dict(row) for row in rows.mappings()]


async def eligible_assignees(
    db: AsyncSession, department_id: int, roles: set[str], limit: int, offset: int
) -> list[dict[str, Any]]:
    rows = await db.execute(
        select(User.app_user_id.label("user_id"), User.username, Employee.full_name)
        .join(Employee, Employee.employee_id == User.employee_id)
        .join(Assignment, Assignment.employee_id == Employee.employee_id)
        .join(Department, Department.department_id == Assignment.department_id)
        .join(UserRole, UserRole.app_user_id == User.app_user_id)
        .join(Role, Role.role_id == UserRole.role_id)
        .where(
            Department.department_id == department_id,
            Role.role_code.in_(roles),
            *current_assignment_conditions(),
        )
        .distinct()
        .order_by(User.app_user_id)
        .limit(limit)
        .offset(offset)
    )
    return [dict(row) for row in rows.mappings()]
