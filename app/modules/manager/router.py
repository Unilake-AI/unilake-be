"""API chỉ đọc dành cho manager ba phòng, báo cáo nghiệp vụ lấy từ Gold."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import require_role
from app.core.exceptions import AppException
from app.core.responses import COMMON_ERROR_RESPONSES
from app.core.roles import MANAGER_SCOPES
from app.dependencies import get_db
from app.modules.manager import exploration, history
from app.modules.manager.access import ManagerAccess, manager_access
from app.modules.manager.schemas import (
    AnalyticsFilters,
    ManagerContext,
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
    dataset_count = await repository.dataset_count(db, manager, filters)
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
        datasets=dataset_count,
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


@router.get("/catalog", response_model=exploration.ExplorerPage)
async def catalog(
    db: DB, manager: Manager, filters: Annotated[ReportFilters, Query()]
) -> exploration.ExplorerPage:
    return await exploration.catalog(db, manager, filters)


@router.get("/catalog/{dataset_id}", response_model=exploration.ExplorerDataset)
async def catalog_detail(
    dataset_id: Annotated[int, Path(gt=0)], db: DB, manager: Manager
) -> exploration.ExplorerDataset:
    return await exploration.dataset(db, manager, dataset_id)


@router.get("/catalog/{dataset_id}/lineage", response_model=exploration.DatasetLineage)
async def catalog_lineage(
    dataset_id: Annotated[int, Path(gt=0)], db: DB, manager: Manager
) -> exploration.DatasetLineage:
    return await exploration.lineage(db, manager, dataset_id)


@router.get("/analysis-options")
async def analysis_options(
    db: DB, manager: Manager, filters: Annotated[ReportFilters, Query()]
) -> list[dict[str, Any]]:
    return await repository.analysis_options(db, manager, filters)


@router.get("/publication-trend")
async def publication_trend(
    db: DB, manager: Manager, filters: Annotated[ReportFilters, Query()]
) -> list[dict[str, Any]]:
    return await repository.publication_trend(db, manager, filters)


@router.get("/query-history", response_model=history.QueryHistoryPage)
async def query_history(
    db: DB, manager: Manager, filters: Annotated[ReportFilters, Query()]
) -> history.QueryHistoryPage:
    return await history.list_history(db, manager, filters)


@router.get("/query-history/{query_id}", response_model=history.QueryHistoryDetail)
async def query_history_detail(
    query_id: UUID, db: DB, manager: Manager
) -> history.QueryHistoryDetail:
    return await history.detail(db, manager, query_id)


@router.get("/ai-capabilities")
async def ai_capabilities(manager: Manager) -> dict[str, Any]:
    return {
        "configured": settings.llm_provider.lower() == "openai"
        and bool(settings.llm_api_key and settings.llm_model),
        "max_rows": 200,
        "data_layer": "GOLD",
        "note": "Truy vấn thống kê Gold trong phạm vi phòng ban. Chưa hỗ trợ điểm trung bình, tỷ lệ đạt và kết luận nguyên nhân.",
    }
