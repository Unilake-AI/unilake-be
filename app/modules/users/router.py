"""ADMIN cấu hình liên kết tài khoản và phân công tổ chức."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_role
from app.core.roles import RoleCode
from app.dependencies import get_db
from app.modules.users.models import Position
from app.modules.users.schemas import (
    AccountAccessUpdate,
    AccountCreate,
    AssignmentCreate,
    EmployeeLink,
    IAMAuditEvent,
)
from app.repositories import account_configuration_repo as repo
from app.repositories.department_access_repo import active_departments

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

require_admin = require_role(RoleCode.ADMIN)


class UserAdminRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as exc:
                # Validation errors can otherwise echo credential fields or a whole request.
                return JSONResponse(
                    status_code=422,
                    content={
                        "detail": [
                            {
                                "loc": item["loc"][:-1]
                                if item["type"] == "extra_forbidden"
                                else item["loc"],
                                "type": item["type"],
                                "msg": "Dữ liệu yêu cầu không hợp lệ",
                            }
                            for item in exc.errors()
                        ]
                    },
                )

        return handle


router = APIRouter(
    prefix="/users",
    tags=["users"],
    dependencies=[Depends(require_admin)],
    route_class=UserAdminRoute,
)
DB = Annotated[AsyncSession, Depends(get_db)]
Admin = Annotated[dict[str, Any], Depends(require_admin)]


@router.get("")
async def list_accounts(
    db: DB,
    search: str = "",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, Any]]:
    return await repo.accounts(db, search, limit, offset)


@router.post("", status_code=201)
async def create_account(request: AccountCreate, db: DB, admin: Admin) -> dict[str, int]:
    actor_id = int(admin["sub"])
    async with repo.audit_rejection(db, actor_id, "ACCOUNT_CREATED"):
        return {"user_id": await repo.create_account(db, actor_id, request)}


@router.patch("/{user_id}/access", status_code=204)
async def update_access(
    user_id: Annotated[int, Path(gt=0)], request: AccountAccessUpdate, db: DB, admin: Admin
) -> Response:
    actor_id = int(admin["sub"])
    async with repo.audit_rejection(db, actor_id, "ACCOUNT_ACCESS_UPDATED", user_id):
        await repo.update_access(db, actor_id, user_id, request)
    return Response(status_code=204)


@router.get("/summary")
async def account_summary(db: DB) -> dict[str, Any]:
    return await repo.account_summary(db)


@router.get("/audit", response_model=list[IAMAuditEvent])
async def audit(
    db: DB,
    response: Response,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, Any]]:
    response.headers["Cache-Control"] = "no-store"
    return await repo.audit_events(db, limit, offset)


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
    user_id: Annotated[int, Path(gt=0)], request: EmployeeLink, db: DB, admin: Admin
) -> Response:
    actor_id = int(admin["sub"])
    async with repo.audit_rejection(db, actor_id, "EMPLOYEE_LINKED", user_id):
        await repo.link_employee(db, user_id, request)
        await repo.record_event(
            db,
            actor_id,
            "EMPLOYEE_LINKED",
            user_id,
            {"employee_id": {"before": request.expected_employee_id, "after": request.employee_id}},
        )
    return Response(status_code=204)


@router.post("/assignments", status_code=201)
async def add_assignment(request: AssignmentCreate, db: DB, admin: Admin) -> dict[str, str]:
    actor_id = int(admin["sub"])
    async with repo.audit_rejection(db, actor_id, "ASSIGNMENT_CREATED"):
        assignment_id = await repo.add_assignment(db, request)
        await repo.record_event(
            db,
            actor_id,
            "ASSIGNMENT_CREATED",
            assignment_id,
            request.model_dump(
                mode="json",
                include={
                    "employee_id",
                    "department_id",
                    "position_id",
                    "start_date",
                    "end_date",
                    "is_primary",
                },
            ),
        )
    return {"status": "created"}
