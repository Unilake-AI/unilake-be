"""ADMIN cấu hình liên kết tài khoản và phân công tổ chức."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_role
from app.core.roles import RoleCode
from app.dependencies import get_db
from app.modules.users.models import Position
from app.modules.users.schemas import AssignmentCreate, EmployeeLink  # noqa: TC001
from app.repositories import account_configuration_repo as repo
from app.repositories.department_access_repo import active_departments

router = APIRouter(
    prefix="/users", tags=["users"], dependencies=[Depends(require_role(RoleCode.ADMIN))]
)
DB = Annotated[AsyncSession, Depends(get_db)]


@router.get("")
async def list_accounts(
    db: DB,
    search: str = "",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, Any]]:
    return await repo.accounts(db, search, limit, offset)


@router.get("/employees")
async def list_employees(
    db: DB,
    search: str = "",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, Any]]:
    return await repo.employees(db, search, limit, offset)


@router.get("/organization")
async def organization(db: DB) -> dict[str, Any]:
    positions = await db.scalars(select(Position).order_by(Position.position_name))
    return {
        "departments": await active_departments(db),
        "positions": [
            {"position_id": p.position_id, "position_name": p.position_name} for p in positions
        ],
    }


@router.get("/employees/{employee_id}/assignments")
async def assignments(employee_id: Annotated[int, Path(gt=0)], db: DB) -> list[dict[str, Any]]:
    return await repo.employee_assignments(db, employee_id)


@router.patch("/{user_id}/employee", status_code=204)
async def link_employee(
    user_id: Annotated[int, Path(gt=0)], request: EmployeeLink, db: DB
) -> Response:
    await repo.link_employee(db, user_id, request)
    return Response(status_code=204)


@router.post("/assignments", status_code=201)
async def add_assignment(request: AssignmentCreate, db: DB) -> dict[str, str]:
    await repo.add_assignment(db, request)
    return {"status": "created"}
