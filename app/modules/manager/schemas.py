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


class GoldSource(BaseModel):
    database_sync_status: str | None = None
    database_sync_error: str | None = None
    scope: str
    silver_snapshot_id: str
    job_id: str | None
    sync_status: str
    finished_at: datetime | None
    error_code: str | None


class ManagerDataset(BaseModel):
    dataset_id: int
    dataset_code: str
    dataset_name: str
    domain: str
    owner_department_id: int
    description: str | None
    sources: list[GoldSource]


class DatasetPage(BaseModel):
    items: list[ManagerDataset]
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


class DatabaseSyncStats(BaseModel):
    total: int = 0
    synced: int = 0
    not_started: int = 0
    queued: int = 0
    running: int = 0
    blocked: int = 0
    failed: int = 0
    superseded: int = 0


class ManagerOverview(BaseModel):
    data_layer: Literal["GOLD"] = "GOLD"
    status: Literal["READY", "PARTIAL", "PENDING", "NO_DATA"]
    datasets: int
    sources: SourceStats
    database_sync: DatabaseSyncStats = Field(default_factory=DatabaseSyncStats)
    last_updated_at: datetime | None
    kpis: MetricPage
    issues: staff_schemas.IssueStats
    note: str = "Số bản ghi theo từng nguồn Gold; không cộng thành số người duy nhất."


class RecordFilters(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    dataset_id: int | None = Field(default=None, gt=0)
    search: str | None = Field(default=None, min_length=1, max_length=100)
    status: str | None = Field(default=None, min_length=1, max_length=30)
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class RecordType(BaseModel):
    entity: str
    primary_key: str
    fields: list[str]
    search_fields: list[str]
    status_field: str | None


class RecordSource(BaseModel):
    dataset_id: int
    dataset_name: str
    gold_job_id: str
    silver_snapshot_id: str
    published_at: datetime
    synced_at: datetime


class ManagerRecord(BaseModel):
    record_id: int
    values: dict[str, Any]
    source: RecordSource


class RecordPage(BaseModel):
    data_layer: Literal["GOLD"] = "GOLD"
    domain: Domain
    entity: str
    items: list[ManagerRecord]
    total: int
    limit: int
    offset: int
