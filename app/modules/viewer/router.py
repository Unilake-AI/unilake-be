"""Tám API báo cáo chỉ đọc dành cho VIEWER."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query

from app.core.exceptions import AppException
from app.core.responses import COMMON_ERROR_RESPONSES
from app.modules.viewer import service
from app.modules.viewer.dependencies import DB, Viewer  # noqa: TC001
from app.modules.viewer.schemas import (
    DomainOverview,  # noqa: TC001
    QualityOverview,  # noqa: TC001
    ViewerContext,  # noqa: TC001
    ViewerIssueFilters,  # noqa: TC001
    ViewerIssuePage,  # noqa: TC001
    ViewerOverview,  # noqa: TC001
)
from app.repositories import report_read_repo, viewer_report_repo
from app.repositories.report_read_repo import ExplorerDataset, ExplorerPage  # noqa: TC001
from app.shared.reporting import Domain, GoldAnalyticsFilters, MetricPage, ReportFilters

router = APIRouter(prefix="/viewer", tags=["viewer"], responses=COMMON_ERROR_RESPONSES)


@router.get("/context")
async def context(viewer: Viewer) -> ViewerContext:
    return viewer


@router.get("/overview")
async def overview(_viewer: Viewer, db: DB) -> ViewerOverview:
    return await service.overview(db)


@router.get("/domains/{domain}/overview")
async def domain_overview(domain: Domain, _viewer: Viewer, db: DB) -> DomainOverview:
    return await service.domain_overview(db, domain)


@router.get("/domains/{domain}/analytics")
async def analytics(
    domain: Domain, _viewer: Viewer, db: DB, filters: Annotated[GoldAnalyticsFilters, Query()]
) -> MetricPage:
    scope = viewer_report_repo.dataset_scope(domain, filters.dataset_id)
    return await report_read_repo.metrics(db, scope, filters)


@router.get("/datasets")
async def datasets(
    _viewer: Viewer, db: DB, filters: Annotated[ReportFilters, Query()]
) -> ExplorerPage:
    scope = viewer_report_repo.dataset_scope(filters.domain, filters.dataset_id)
    return await report_read_repo.catalog(db, scope, filters)


@router.get("/datasets/{dataset_id}")
async def dataset(
    dataset_id: Annotated[int, Path(gt=0, le=2**63 - 1)], _viewer: Viewer, db: DB
) -> ExplorerDataset:
    scope = viewer_report_repo.dataset_scope(dataset_id=dataset_id)
    page = await report_read_repo.catalog(db, scope, ReportFilters(dataset_id=dataset_id, limit=1))
    if not page.items:
        raise AppException(
            "Không tìm thấy bộ dữ liệu trong phạm vi được xem",
            code="DATASET_NOT_FOUND",
            status_code=404,
        )
    return page.items[0]


@router.get("/quality/overview")
async def quality_overview(_viewer: Viewer, db: DB) -> QualityOverview:
    return await service.quality_overview(db)


@router.get("/quality/issues")
async def quality_issues(
    _viewer: Viewer, db: DB, filters: Annotated[ViewerIssueFilters, Query()]
) -> ViewerIssuePage:
    return await viewer_report_repo.issues(db, filters)
