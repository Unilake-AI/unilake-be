"""Hợp đồng đọc thống kê dùng chung cho các vai trò báo cáo."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Domain = Literal["hr", "academic", "admissions"]
DOMAIN_NAMES: dict[Domain, str] = {
    "hr": "Nhân sự",
    "academic": "Đào tạo",
    "admissions": "Tuyển sinh",
}


DatasetID = Annotated[int, Field(gt=0, le=2**63 - 1)]


class DatasetFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: DatasetID | None = None
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=2**63 - 1)


class ReportFilters(DatasetFilters):
    domain: Domain | None = None


class GoldAnalyticsFilters(DatasetFilters):
    entity: str | None = Field(default=None, min_length=1, max_length=150)
    metric: str = Field(default="record_count", min_length=1, max_length=100)
    dimension_value: str | None = Field(default=None, max_length=150)


class AnalyticsFilters(GoldAnalyticsFilters, ReportFilters):
    pass


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
