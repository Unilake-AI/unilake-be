"""Các schema Pydantic cho job ingestion."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class IngestionStart(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    data_source_id: int = Field(gt=0, strict=True)
    dataset_id: int = Field(gt=0, strict=True)
    source_table: str = Field(min_length=1, max_length=200)
    # API nhận JSON; service kiểm tra strict contract và binding trước enqueue.
    validation_policy: dict[str, Any] | None = None


class IngestionJobRead(BaseModel):
    """Dữ liệu trạng thái job ingestion."""

    model_config = ConfigDict(from_attributes=True)
    ingestion_job_id: int
    data_source_id: int
    dataset_id: int
    status: str
    stage: str | None
    job_type: str | None = None
    source_table: str | None
    created_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    requested_by: str | None
    rows_read: int | None
    rows_written: int | None
    rows_rejected: int | None
    error_message: str | None
    output: dict[str, Any] | None
    validation_report: dict[str, Any] | None = None
    root_job_id: int | None = None
    # Job cũ chỉ xuất hiện trước khi migration hoàn tất mới có thể chưa mang số lần chạy.
    attempt_number: int | None = None


class IngestionJobStats(BaseModel):
    total: int
    queued: int
    running: int
    success: int
    failed: int


class IngestionJobMetrics(BaseModel):
    average_wait_seconds: float | None
    average_processing_seconds: float | None
    failure_rate_percent: float | None
    oldest_queued_seconds: float | None


class IngestionWorkerRead(BaseModel):
    worker_id: str
    status: str
    current_job_id: int | None
    last_heartbeat_at: datetime


class IngestionAttemptRead(BaseModel):
    ingestion_job_id: int
    root_job_id: int | None
    attempt_number: int
    status: str
    stage: str | None
    error_message: str | None
    created_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
