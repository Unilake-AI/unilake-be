"""Role và phân công hiện hành cùng xác định phạm vi trưởng phòng."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.roles import MANAGER_SCOPES
from app.modules.manager.schemas import ManagerContext
from app.modules.staff.contracts import schemas as staff_schemas
from app.repositories.department_access_repo import active_department_choices, active_roles

DOMAIN_NAMES = {"hr": "Nhân sự", "academic": "Đào tạo", "admissions": "Tuyển sinh"}


@dataclass(frozen=True)
class ManagerAccess:
    context: ManagerContext
    # Ghép đúng lĩnh vực với phòng đang được phân công, tránh quyền chéo khi nhiều role.
    scopes: tuple[tuple[str, int], ...]

    def require_scope(self, domain: str | None = None) -> None:
        if not self.scopes or (domain is not None and domain not in dict(self.scopes)):
            raise AppException(
                "Cần role manager và phân công đang hiệu lực tại phòng phụ trách lĩnh vực này",
                code="MANAGER_SCOPE_REQUIRED",
                status_code=403,
            )


async def manager_access(db: AsyncSession, user: dict[str, Any]) -> ManagerAccess:
    user_id = int(user["sub"])
    try:
        roles = (await active_roles(db, user_id)).intersection(user.get("roles", []))
        managers = {role: spec for role, spec in MANAGER_SCOPES.items() if role in roles}
        if not managers:
            raise AppException(
                "Chỉ trưởng phòng được xem báo cáo này", code="FORBIDDEN", status_code=403
            )
        departments = await active_department_choices(db, user_id)
    except SQLAlchemyError:
        raise AppException(
            "Không thể đọc quyền quản lý phòng ban", code="MANAGER_UNAVAILABLE", status_code=503
        ) from None
    scopes = tuple(
        (domain, row["department_id"])
        for domain, code in managers.values()
        for row in departments
        if row["department_code"] == code
    )
    allowed_ids = {department for _, department in scopes}
    return ManagerAccess(
        ManagerContext(
            user_id=user_id,
            roles=sorted(roles),
            domains=[
                staff_schemas.DomainChoice(domain=domain, name=DOMAIN_NAMES[domain])
                for domain, _ in managers.values()
            ],
            departments=[
                staff_schemas.DepartmentChoice(**row)
                for row in departments
                if row["department_id"] in allowed_ids
            ],
            default_domain=next(iter(managers.values()))[0] if len(managers) == 1 else None,
            needs_department_assignment=len({domain for domain, _ in scopes}) < len(managers),
            note="Gold chỉ đọc từ dataset đang hoạt động thuộc phòng sở hữu được phân công. PNS → hr, PDT → academic, PTS → admissions. Dataset chưa gán phòng sở hữu không được hiển thị.",
        ),
        scopes,
    )
