"""Hợp đồng đọc báo cáo Gold của trưởng phòng."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.staff.contracts import schemas as staff_schemas

Domain = Literal["hr", "academic", "admissions"]


class ManagerContext(BaseModel):
    user_id: int
    roles: list[str]
    domains: list[staff_schemas.DomainChoice]
    departments: list[staff_schemas.DepartmentChoice]
    default_domain: str | None
    needs_department_assignment: bool = False
    needs_department_configuration: bool = False
    data_layer: Literal["GOLD"] = "GOLD"
    note: str


class ReportFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: Domain | None = None
    dataset_id: int | None = Field(default=None, gt=0)
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class AnalyticsFilters(ReportFilters):
    entity: str | None = Field(default=None, min_length=1, max_length=150)
    metric: str = Field(default="record_count", min_length=1, max_length=100)
    dimension_value: str | None = Field(default=None, max_length=150)


class ManagerIssueFilters(staff_schemas.IssueFilters):
    model_config = ConfigDict(extra="forbid")
    domain: Domain | None = None
    dataset_id: int | None = Field(default=None, gt=0)


class GoldMetric(BaseModel):
    dataset_id: int
    dataset_name: str
    domain: str
    scope: str
    gold_snapshot_id: str
    silver_snapshot_id: str
    recipe_version: str
    finished_at: datetime
    entity: str
    metric: str
    dimensions: dict[str, Any]
    value: int
    unit: Literal["records"] = "records"


class MetricPage(BaseModel):
    items: list[GoldMetric]
    total: int
    limit: int
    offset: int


class SourceStats(BaseModel):
    total: int = 0
    current: int = 0
    not_started: int = 0
    queued: int = 0
    running: int = 0
    retry_wait: int = 0
    failed: int = 0
    superseded: int = 0


class ManagerOverview(BaseModel):
    data_layer: Literal["GOLD"] = "GOLD"
    status: Literal["READY", "PARTIAL", "PENDING", "NO_DATA"]
    datasets: int
    sources: SourceStats
    last_updated_at: datetime | None
    kpis: MetricPage
    issues: staff_schemas.IssueStats
    note: str = "Số bản ghi theo từng nguồn Gold; không cộng thành số người duy nhất."
