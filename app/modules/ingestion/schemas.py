"""Các schema Pydantic cho job ingestion."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

IngestionJobStatus = Literal["QUEUED", "RETRY_WAIT", "RUNNING", "SUCCESS", "FAILED", "REJECTED"]


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
    quality_issue_id: str | None = None
    import_type: str | None = None


class UploadFileLayout(BaseModel):
    columns: list[str] = Field(default_factory=list)
    sheet_names: list[str] = Field(default_factory=list)
    sheet_name: str | None = None


class ImportActionCounts(BaseModel):
    inserted: int
    updated: int
    unchanged: int


class ImportPreviewOperation(BaseModel):
    operation_id: int
    table: str
    action: Literal["inserted", "updated", "unchanged"]
    fields: list[str]
    source_row_count: int
    source_rows: list[str]


class BusinessImportPreview(BaseModel):
    ingestion_job_id: int
    status: Literal["PENDING", "READY", "BLOCKED", "NOT_AVAILABLE"]
    database_status: str | None
    database_committed: bool
    database_error: dict[str, Any] | None = None
    error_report: dict[str, Any] | None = None
    attempt_number: int | None = None
    prepared_at: datetime | None = None
    silver_snapshot_id: str | None = None
    format_version: int | None = None
    import_type: str | None = None
    contract_version: str | None = None
    context: dict[str, str] | None = None
    input_rows: int | None = None
    operation_count: int | None = None
    tables: dict[str, ImportActionCounts] | None = None
    operations: list[ImportPreviewOperation] | None = None


class IngestionJobStats(BaseModel):
    total: int
    queued: int
    running: int
    success: int
    failed: int
    retry_wait: int = 0


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
