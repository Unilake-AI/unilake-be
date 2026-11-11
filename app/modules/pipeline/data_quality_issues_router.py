"""Nhận yêu cầu xử lý dữ liệu và chuyển tới dịch vụ của nhân viên."""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.dependencies import get_db
from app.modules.ingestion.contracts.jobs import IngestionJobRead
from app.modules.staff.contracts import quality_issues as service
from app.modules.staff.contracts import schemas

router = APIRouter(prefix="/data-quality-issues", tags=["data-quality-issues"])
DB = Annotated[AsyncSession, Depends(get_db)]
User = Annotated[dict[str, Any], Depends(get_current_user)]


async def _actor(db: DB, user: User) -> service.IssueActor:
    return await service.issue_actor(db, user)


Actor = Annotated[service.IssueActor, Depends(_actor)]


@router.post("", status_code=201, response_model=schemas.IssueDetail)
async def create_issue(request: schemas.CreateIssue, db: DB, actor: Actor) -> schemas.IssueDetail:
    return await service.create_issue(request, db, actor)


@router.get("", response_model=list[schemas.IssueRead])
async def list_issues(
    db: DB, actor: Actor, filters: Annotated[schemas.IssueFilters, Query()]
) -> list[schemas.IssueRead]:
    return await service.list_issues(db, actor, filters)


@router.get(
    "/stats", response_model=schemas.IssueStats, summary="Thống kê yêu cầu trong phạm vi được xem"
)
async def issue_stats(
    db: DB, actor: Actor, filters: Annotated[schemas.IssueFilters, Query()]
) -> schemas.IssueStats:
    return await service.issue_stats(db, actor, filters)


@router.get(
    "/departments",
    response_model=list[schemas.IssueDepartmentChoice],
    summary="Các phòng ban quản trị có thể giao xử lý",
)
async def issue_departments(db: DB, actor: Actor) -> list[schemas.IssueDepartmentChoice]:
    return await service.issue_departments(db, actor)


@router.get(
    "/assignees",
    response_model=list[schemas.AssigneeChoice],
    summary="Chọn người phụ trách đủ quyền và còn phân công",
)
async def issue_assignees(
    db: DB,
    actor: Actor,
    department_id: Annotated[int, Query(gt=0)],
    domain: Literal["hr", "academic", "admissions"],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[schemas.AssigneeChoice]:
    return await service.issue_assignees(db, actor, department_id, domain, limit, offset)


@router.get("/{issue_id}", response_model=schemas.IssueDetail)
async def get_issue(issue_id: UUID, db: DB, actor: Actor) -> schemas.IssueDetail:
    return await service.get_issue(issue_id, db, actor)


@router.get(
    "/{issue_id}/comparison",
    response_model=schemas.IssueComparisonResponse,
    summary="Đối chiếu dữ liệu trước và sau khi sửa theo khóa nghiệp vụ cho quản trị dữ liệu",
)
async def compare_issue(
    issue_id: UUID,
    db: DB,
    actor: Actor,
    response: Response,
    cleaning_run_id: Annotated[
        UUID | None,
        Query(
            description="Mã lượt Cleaning cần đối chiếu; bỏ trống để chọn lượt mới nhất của bản sửa"
        ),
    ] = None,
) -> schemas.IssueComparisonResponse:
    response.headers["Cache-Control"] = "no-store"
    return await service.compare_issue(issue_id, db, actor, cleaning_run_id=cleaning_run_id)


@router.patch("/{issue_id}/assignment", response_model=schemas.IssueDetail)
async def assign_issue(
    issue_id: UUID, request: schemas.AssignIssue, db: DB, actor: Actor
) -> schemas.IssueDetail:
    return await service.assign_issue(issue_id, request, db, actor)


@router.post(
    "/{issue_id}/uploads",
    response_model=IngestionJobRead,
    status_code=202,
    summary="Người phụ trách tải bản đã sửa vào đúng bộ dữ liệu của yêu cầu",
)
async def upload_resolution_file(
    issue_id: UUID,
    db: DB,
    actor: Actor,
    user: User,
    file: Annotated[UploadFile, File(description="Bản đã sửa, giữ tên file nguồn của yêu cầu")],
    parser_options: Annotated[
        str | None, Form(description="Bỏ trống để dùng cách đọc đã ghi nhận của file nguồn")
    ] = None,
) -> IngestionJobRead:
    return await service.upload_resolution_file(issue_id, db, actor, user, file, parser_options)


@router.post("/{issue_id}/resolution", response_model=schemas.IssueDetail)
async def submit_resolution(
    issue_id: UUID, request: schemas.SubmitResolution, db: DB, actor: Actor
) -> schemas.IssueDetail:
    return await service.submit_resolution(issue_id, request, db, actor)


@router.post("/{issue_id}/verification", response_model=schemas.IssueDetail)
async def verify_issue(
    issue_id: UUID, request: schemas.SubmitVerification, db: DB, actor: Actor
) -> schemas.IssueDetail:
    return await service.verify_issue(issue_id, request, db, actor)
