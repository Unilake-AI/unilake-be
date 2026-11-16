"""Cấu hình danh tính và phân công bằng dữ liệu tổ chức hiện có."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import AppException
from app.modules.auth.models import Role, UserRole
from app.modules.users.models import Assignment, Department, Employee, Position, User
from app.repositories.department_access_repo import active_department_choices

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.modules.users.schemas import AssignmentCreate, EmployeeLink


def conflict(message: str) -> AppException:
    return AppException(message, code="ACCOUNT_CONFIGURATION_CONFLICT", status_code=409)


async def accounts(db: AsyncSession, search: str, limit: int, offset: int) -> list[dict[str, Any]]:
    stmt = select(User, Employee).outerjoin(Employee, Employee.employee_id == User.employee_id)
    if search:
        stmt = stmt.where(User.username.ilike(f"%{search}%"))
    rows = (await db.execute(stmt.order_by(User.app_user_id).limit(limit).offset(offset))).all()
    result = []
    for user, employee in rows:
        roles = list(
            await db.scalars(
                select(Role.role_code)
                .join(UserRole)
                .where(UserRole.app_user_id == user.app_user_id)
            )
        )
        result.append(
            {
                "user_id": user.app_user_id,
                "username": user.username,
                "is_active": user.is_active,
                "employee_id": user.employee_id,
                "full_name": employee.full_name if employee else None,
                "roles": sorted(roles),
                "departments": await active_department_choices(db, user.app_user_id),
            }
        )
    return result


async def employees(db: AsyncSession, search: str, limit: int, offset: int) -> list[dict[str, Any]]:
    stmt = select(Employee).where(func.upper(Employee.employee_status) == "ACTIVE")
    if search:
        stmt = stmt.where(
            or_(
                Employee.full_name.ilike(f"%{search}%"), Employee.employee_code.ilike(f"%{search}%")
            )
        )
    rows = await db.scalars(stmt.order_by(Employee.employee_id).limit(limit).offset(offset))
    return [
        {"employee_id": e.employee_id, "employee_code": e.employee_code, "full_name": e.full_name}
        for e in rows
    ]


async def employee_assignments(db: AsyncSession, employee_id: int) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(Assignment, Department.department_name, Position.position_name)
            .join(Department)
            .join(Position)
            .where(Assignment.employee_id == employee_id)
            .order_by(Assignment.start_date.desc())
        )
    ).all()
    return [
        {
            "assignment_id": a.assignment_id,
            "department_id": a.department_id,
            "department_name": department,
            "position_name": position,
            "start_date": a.start_date,
            "end_date": a.end_date,
            "is_primary": a.is_primary,
            "status": a.assignment_status,
        }
        for a, department, position in rows
    ]


async def link_employee(db: AsyncSession, user_id: int, request: EmployeeLink) -> None:
    user = await db.scalar(select(User).where(User.app_user_id == user_id).with_for_update())
    employee = await db.scalar(
        select(Employee).where(Employee.employee_id == request.employee_id).with_for_update()
    )
    if user is None or employee is None or employee.employee_status.upper() != "ACTIVE":
        raise AppException(
            "Không tìm thấy tài khoản hoặc nhân viên đang hoạt động",
            status_code=404,
            code="ACCOUNT_EMPLOYEE_NOT_FOUND",
        )
    if user.employee_id != request.expected_employee_id:
        raise conflict("Liên kết đã thay đổi. Vui lòng tải lại tài khoản.")
    linked = await db.scalar(
        select(User.app_user_id).where(
            User.employee_id == request.employee_id, User.app_user_id != user_id
        )
    )
    if linked is not None:
        raise conflict("Nhân viên này đã liên kết với tài khoản khác")
    user.employee_id = request.employee_id
    try:
        await db.flush()
    except IntegrityError:
        raise conflict("Nhân viên này đã liên kết với tài khoản khác") from None


async def add_assignment(db: AsyncSession, request: AssignmentCreate) -> None:
    employee = await db.scalar(
        select(Employee).where(Employee.employee_id == request.employee_id).with_for_update()
    )
    department = await db.get(Department, request.department_id)
    position = await db.get(Position, request.position_id)
    if (
        employee is None
        or employee.employee_status.upper() != "ACTIVE"
        or department is None
        or department.status != "ACTIVE"
        or position is None
    ):
        raise AppException(
            "Nhân viên, phòng ban hoặc chức vụ không hợp lệ",
            status_code=422,
            code="ASSIGNMENT_INVALID",
        )
    stmt = select(Assignment).where(
        Assignment.employee_id == request.employee_id,
        func.upper(Assignment.assignment_status) == "ACTIVE",
        or_(Assignment.end_date.is_(None), Assignment.end_date >= request.start_date),
    )
    if request.end_date is not None:
        stmt = stmt.where(Assignment.start_date <= request.end_date)
    overlapping = list(await db.scalars(stmt))
    if any(
        a.department_id == request.department_id and a.position_id == request.position_id
        for a in overlapping
    ):
        raise conflict("Đã có phân công cùng phòng và chức vụ trong thời gian này")
    if request.is_primary and any(a.is_primary for a in overlapping):
        raise conflict("Đã có phân công chính trong thời gian này. Hãy chọn phân công bổ sung.")
    db.add(Assignment(**request.model_dump(), assignment_status="ACTIVE"))
    await db.flush()
