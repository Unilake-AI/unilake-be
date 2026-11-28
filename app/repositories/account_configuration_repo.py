"""Cấu hình danh tính và phân công bằng dữ liệu tổ chức hiện có."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    String,
    Table,
    delete,
    func,
    literal,
    null,
    or_,
    select,
    union_all,
    update,
)
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import AppException
from app.core.roles import RoleCode
from app.core.security import hash_password
from app.infra.db.base import Base
from app.modules.auth.models import LoginSession, Role, UserRole
from app.modules.users.models import Assignment, Department, Employee, Position, User
from app.repositories.department_access_repo import (
    active_department_choices,
    active_roles,
    current_assignment_conditions,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.modules.users.schemas import (
        AccountAccessUpdate,
        AccountCreate,
        AssignmentCreate,
        EmployeeLink,
    )

IAM_EVENTS = Table(
    "iam_event",
    Base.metadata,
    Column("event_id", BigInteger, primary_key=True),
    Column("actor_id", BigInteger, ForeignKey("app.app_user.app_user_id"), nullable=False),
    Column("action", String(40), nullable=False),
    Column("target_id", BigInteger),
    Column("outcome", String(12), nullable=False),
    Column("changes", JSON),
    Column("error_code", String(100)),
    Column("occurred_at", DateTime, nullable=False, server_default=func.now()),
    schema="app",
)


def conflict(message: str) -> AppException:
    return AppException(message, code="ACCOUNT_CONFIGURATION_CONFLICT", status_code=409)


async def record_event(
    db: AsyncSession, actor_id: int, action: str, target_id: int, changes: dict[str, Any]
) -> None:
    await db.execute(
        IAM_EVENTS.insert().values(
            actor_id=actor_id,
            action=action,
            target_id=target_id,
            outcome="SUCCESS",
            changes=changes,
        )
    )


@asynccontextmanager
async def audit_rejection(
    db: AsyncSession, actor_id: int, action: str, target_id: int | None = None
) -> AsyncIterator[None]:
    try:
        yield
    except AppException as exc:
        # Persist the rejection only after rolling back the attempted mutation.
        await db.rollback()
        await db.execute(
            IAM_EVENTS.insert().values(
                actor_id=actor_id,
                action=action,
                target_id=target_id,
                outcome="REJECTED",
                error_code=exc.code,
            )
        )
        await db.commit()
        raise


async def audit_events(db: AsyncSession, limit: int, offset: int) -> list[dict[str, Any]]:
    events = union_all(
        select(
            IAM_EVENTS.c.event_id,
            IAM_EVENTS.c.actor_id,
            IAM_EVENTS.c.action,
            IAM_EVENTS.c.target_id,
            IAM_EVENTS.c.occurred_at,
            IAM_EVENTS.c.outcome,
            IAM_EVENTS.c.changes,
            IAM_EVENTS.c.error_code,
        ),
        select(
            LoginSession.login_session_id,
            LoginSession.app_user_id,
            literal("LOGIN"),
            LoginSession.app_user_id,
            LoginSession.login_at,
            literal("SUCCESS"),
            null(),
            null(),
        ),
    ).subquery()
    rows = await db.execute(
        select(events, User.username)
        .join(User, User.app_user_id == events.c.actor_id)
        .order_by(events.c.occurred_at.desc(), events.c.action, events.c.event_id.desc())
        .limit(limit)
        .offset(offset)
    )
    return [dict(row) for row in rows.mappings()]


async def account_summary(db: AsyncSession) -> dict[str, Any]:
    total, active = (
        await db.execute(
            select(
                func.count(User.app_user_id), func.count(User.app_user_id).filter(User.is_active)
            )
        )
    ).one()
    department_rows = await db.execute(
        select(
            Department.department_name, func.count(User.app_user_id.distinct()).label("accounts")
        )
        .select_from(User)
        .join(Employee, Employee.employee_id == User.employee_id)
        .join(Assignment, Assignment.employee_id == Employee.employee_id)
        .join(Department, Department.department_id == Assignment.department_id)
        .where(*current_assignment_conditions())
        .group_by(Department.department_id, Department.department_name)
        .order_by(Department.department_name, Department.department_id)
    )
    return {
        "total": total,
        "active": active,
        "departments": [dict(row) for row in department_rows.mappings()],
    }


async def _role_ids(db: AsyncSession, roles: set[RoleCode]) -> dict[str, int]:
    result = dict(
        (
            await db.execute(select(Role.role_code, Role.role_id).where(Role.role_code.in_(roles)))
        ).all()
    )
    if result.keys() != roles:
        raise AppException(
            "Vai trò chưa được cấu hình", code="ACCOUNT_ROLE_UNAVAILABLE", status_code=422
        )
    return result


async def create_account(db: AsyncSession, actor_id: int, request: AccountCreate) -> int:
    password = request.password.get_secret_value()
    if not 12 <= len(password) <= 256:
        raise AppException(
            "Mật khẩu phải có từ 12 đến 256 ký tự", code="INVALID_PASSWORD", status_code=422
        )
    password_hash = await asyncio.to_thread(hash_password, password)
    await _lock_admin_access(db, actor_id)
    role_ids = await _role_ids(db, request.roles)
    user = User(username=request.username, password_hash=password_hash, is_active=True)
    db.add(user)
    try:
        await db.flush()
    except IntegrityError:
        raise conflict("Tên đăng nhập đã tồn tại") from None
    db.add_all(
        [UserRole(app_user_id=user.app_user_id, role_id=role_id) for role_id in role_ids.values()]
    )
    await record_event(
        db,
        actor_id,
        "ACCOUNT_CREATED",
        user.app_user_id,
        {"roles": sorted(request.roles), "is_active": True},
    )
    return user.app_user_id


async def _lock_admin_access(db: AsyncSession, actor_id: int) -> None:
    # Serialize access updates and recheck the actor after any concurrent role change.
    await db.scalar(select(Role.role_id).where(Role.role_code == RoleCode.ADMIN).with_for_update())
    if RoleCode.ADMIN not in await active_roles(db, actor_id):
        raise AppException(
            "Bạn không có quyền quản trị tài khoản", code="FORBIDDEN", status_code=403
        )


async def update_access(
    db: AsyncSession, actor_id: int, user_id: int, request: AccountAccessUpdate
) -> None:
    await _lock_admin_access(db, actor_id)
    if actor_id == user_id and (not request.is_active or RoleCode.ADMIN not in request.roles):
        raise conflict("Bạn phải giữ quyền ADMIN và trạng thái hoạt động cho tài khoản hiện tại")
    user = await db.scalar(select(User).where(User.app_user_id == user_id).with_for_update())
    if user is None:
        raise AppException("Không tìm thấy tài khoản", code="ACCOUNT_NOT_FOUND", status_code=404)
    current_roles = dict(
        (
            await db.execute(
                select(Role.role_code, Role.role_id)
                .join(UserRole)
                .where(UserRole.app_user_id == user_id)
            )
        ).all()
    )
    if (
        user.is_active != request.expected_is_active
        or current_roles.keys() != request.expected_roles
    ):
        raise conflict("Quyền truy cập đã thay đổi. Vui lòng tải lại tài khoản.")
    owned = set(current_roles.values())
    role_ids = set((await _role_ids(db, request.roles)).values())
    if removed := owned - role_ids:
        await db.execute(
            delete(UserRole).where(UserRole.app_user_id == user_id, UserRole.role_id.in_(removed))
        )
    db.add_all([UserRole(app_user_id=user_id, role_id=role_id) for role_id in role_ids - owned])
    if role_ids != owned:
        await record_event(
            db,
            actor_id,
            "ROLES_UPDATED",
            user_id,
            {
                "roles": {
                    "before": sorted(current_roles.keys() & set(RoleCode)),
                    "after": sorted(request.roles),
                }
            },
        )
    if user.is_active != request.is_active:
        user.is_active = request.is_active
        if not request.is_active:
            await db.execute(
                update(LoginSession)
                .where(LoginSession.app_user_id == user_id, LoginSession.session_status == "ACTIVE")
                .values(session_status="REVOKED", logout_at=func.now(), refresh_token_hash=None)
            )
        await record_event(
            db,
            actor_id,
            "ACCOUNT_UNLOCKED" if request.is_active else "ACCOUNT_LOCKED",
            user_id,
            {"is_active": {"before": request.expected_is_active, "after": request.is_active}},
        )


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


async def add_assignment(db: AsyncSession, request: AssignmentCreate) -> int:
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
    assignment = Assignment(**request.model_dump(), assignment_status="ACTIVE")
    db.add(assignment)
    await db.flush()
    return assignment.assignment_id
