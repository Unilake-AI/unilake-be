"""Hợp đồng API chỉ đọc của VIEWER."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.staff.contracts import schemas as staff_schemas  # noqa: TC001
from app.shared.reporting import Domain, ReportFilters

GoldStatus = Literal["READY", "PARTIAL", "PENDING", "NO_DATA"]


class ViewerDomain(BaseModel):
    domain: Domain
    name: str


class ViewerContext(BaseModel):
    user_id: int
    roles: list[str]
    scope: Literal["UNIVERSITY"] = "UNIVERSITY"
    data_layer: Literal["GOLD"] = "GOLD"
    domains: list[ViewerDomain]


class PublicationStats(BaseModel):
    total: int = Field(0, description="Số nguồn theo phạm vi xử lý Gold.")
    current: int = Field(0, description="Số nguồn có Gold hiện hành.")
    not_current: int = Field(
        0, description="Số nguồn đã có phạm vi xử lý và đang thiếu Gold hiện hành."
    )
    gold_job_failed: int = Field(0, description="Số nguồn có lượt tạo Gold mới nhất thất bại.")
    sync_failed: int = Field(
        0, description="Số nguồn có Gold hiện hành bị lỗi hoặc chặn đồng bộ nghiệp vụ."
    )
    datasets_without_scope: int = Field(0, description="Số dataset chưa có phạm vi xử lý Gold.")


class HighlightMetric(BaseModel):
    label: str = "Số bản ghi"
    value: int
    unit: Literal["records"] = "records"
    dataset_id: int
    scope: str
    entity: str


class DomainOverview(ViewerDomain):
    status: GoldStatus
    datasets: int
    sources: PublicationStats
    last_published_at: datetime | None
    highlight_metric: HighlightMetric | None = Field(
        description="Số bản ghi theo một dataset, một nguồn và một thực thể khi domain READY."
    )
    message: str | None


class ViewerSummary(BaseModel):
    datasets: int
    sources: int
    ready_domains: int


class ViewerOverview(BaseModel):
    summary: ViewerSummary
    domains: list[DomainOverview]


class QualityOverview(BaseModel):
    quality: staff_schemas.IssueStats
    publication: PublicationStats
    freshness: dict[Domain, datetime | None]


class ViewerIssueFilters(ReportFilters):
    status: staff_schemas.IssueStatus | None = None


class ViewerIssue(BaseModel):
    issue_id: UUID
    dataset_id: int
    department_id: int
    domain: Domain
    status: staff_schemas.IssueStatus
    created_at: datetime
    updated_at: datetime


class ViewerIssuePage(BaseModel):
    items: list[ViewerIssue]
    total: int
    limit: int
    offset: int
