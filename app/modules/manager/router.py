"""API chỉ đọc dành cho manager ba phòng, báo cáo nghiệp vụ lấy từ Gold."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_role
from app.core.exceptions import AppException
from app.core.responses import COMMON_ERROR_RESPONSES
from app.core.roles import MANAGER_SCOPES
from app.dependencies import get_db
from app.modules.manager.access import ManagerAccess, manager_access
from app.modules.manager.schemas import (
    AnalyticsFilters,
    DatasetPage,
    ManagerContext,
    ManagerDataset,
    ManagerIssueFilters,
    ManagerOverview,
    MetricPage,
    ReportFilters,
)
from app.modules.staff.contracts import schemas as staff_schemas
from app.repositories import manager_report_repo as repository

router = APIRouter(prefix="/manager", tags=["manager"], responses=COMMON_ERROR_RESPONSES)
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


@router.get("/context", response_model=ManagerContext)
async def context(manager: Manager) -> ManagerContext:
    return manager.context


@router.get("/overview", response_model=ManagerOverview)
async def overview(
    db: DB, manager: Manager, filters: Annotated[ReportFilters, Query()]
) -> ManagerOverview:
    # KPI trang này chỉ dùng tổng bản ghi; các phép nhóm được đọc qua /analytics.
    kpis = await repository.metrics(db, manager, AnalyticsFilters(**filters.model_dump()))
    sources = await repository.source_stats(db, manager, filters)
    page = await repository.datasets(
        db, manager, ReportFilters(domain=filters.domain, dataset_id=filters.dataset_id, limit=1)
    )
    stats = await repository.issue_stats(
        db, manager, ManagerIssueFilters(domain=filters.domain, dataset_id=filters.dataset_id)
    )
    status = "NO_DATA"
    if sources.total:
        status = (
            "READY"
            if sources.current == sources.total
            else "PARTIAL"
            if sources.current
            else "PENDING"
        )
    return ManagerOverview(
        status=status,
        datasets=page.total,
        sources=sources,
        last_updated_at=await repository.last_updated(db, manager, filters),
        kpis=kpis,
        issues=stats,
    )


@router.get("/analytics", response_model=MetricPage)
async def analytics(
    db: DB, manager: Manager, filters: Annotated[AnalyticsFilters, Query()]
) -> MetricPage:
    return await repository.metrics(db, manager, filters)


@router.get("/datasets", response_model=DatasetPage)
async def datasets(
    db: DB, manager: Manager, filters: Annotated[ReportFilters, Query()]
) -> DatasetPage:
    return await repository.datasets(db, manager, filters)


@router.get("/datasets/{dataset_id}", response_model=ManagerDataset)
async def dataset_detail(
    dataset_id: Annotated[int, Path(gt=0)], db: DB, manager: Manager
) -> ManagerDataset:
    page = await repository.datasets(db, manager, ReportFilters(dataset_id=dataset_id))
    if not page.items:
        raise AppException("Không tìm thấy bộ dữ liệu", code="DATASET_NOT_FOUND", status_code=404)
    return page.items[0]


@router.get("/issues", response_model=list[staff_schemas.IssueRead])
async def issues(
    db: DB, manager: Manager, filters: Annotated[ManagerIssueFilters, Query()]
) -> list[staff_schemas.IssueRead]:
    return await repository.issues(db, manager, filters)


@router.get("/issues/stats", response_model=staff_schemas.IssueStats)
async def issue_stats(
    db: DB, manager: Manager, filters: Annotated[ManagerIssueFilters, Query()]
) -> staff_schemas.IssueStats:
    return await repository.issue_stats(db, manager, filters)


@router.get("/issues/{issue_id}", response_model=staff_schemas.IssueDetail)
async def issue_detail(issue_id: UUID, db: DB, manager: Manager) -> staff_schemas.IssueDetail:
    result = await repository.issue_detail(db, manager, issue_id)
    if result is None:
        raise AppException(
            "Không tìm thấy yêu cầu xử lý", code="QUALITY_ISSUE_NOT_FOUND", status_code=404
        )
    return result
