"""Chiếu bằng chứng đã lưu thành các bước vận hành; không suy đoán % hoàn thành."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

StepState = Literal["done", "running", "waiting", "pending", "failed", "blocked"]


class FlowStep(BaseModel):
    key: str
    label: str
    state: StepState = "pending"
    detail: str = "Chưa bắt đầu"
    at: datetime | None = None


class IngestionFlow(BaseModel):
    ingestion_job_id: int
    dataset_id: int
    dataset_name: str
    domain: str
    filename: str | None
    uploaded_by: str | None
    created_at: datetime | None
    ingestion_status: str
    bronze_rows: int | None
    auto_job_id: str | None
    silver_status: str | None
    attempt_number: int = 0
    next_attempt_at: datetime | None = None
    error_code: str | None = None
    run_id: str | None = None
    snapshot_id: str | None = None
    records_read: int | None = None
    records_passed: int | None = None
    records_quarantined: int | None = None
    records_published: int | None = None
    steps: list[FlowStep] = Field(default_factory=list)


class FlowPage(BaseModel):
    items: list[IngestionFlow]
    next_offset: int | None


def project_flow(row: dict[str, Any], automatic_enabled: bool) -> IngestionFlow:
    row = dict(row)
    result = row.get("result") or {}
    for key in ("records_read", "records_passed", "records_quarantined"):
        if row.get(key) is None:
            row[key] = result.get(key)
    if row.get("records_published") is None:
        row["records_published"] = (result.get("publication") or {}).get("records_published")
    flow = IngestionFlow.model_validate(row)
    received = FlowStep(
        key="received",
        label="Tiếp nhận",
        state="done",
        detail="Đã ghi nhận yêu cầu nạp",
        at=flow.created_at,
    )
    bronze = FlowStep(key="bronze", label="Lưu Bronze")
    queue = FlowStep(key="queue", label="Xếp hàng Silver")
    cleaning = FlowStep(key="cleaning", label="Làm sạch & kiểm tra")
    publication = FlowStep(key="publication", label="Công bố Silver")
    flow.steps = [received, bronze, queue, cleaning, publication]
    if flow.ingestion_status != "SUCCESS":
        if flow.ingestion_status == "FAILED":
            bronze.state, bronze.detail = (
                "failed",
                "Nạp dữ liệu thất bại; Silver chưa bắt đầu. Xem chi tiết lần nạp.",
            )
            bronze.at = row.get("bronze_finished_at")
        elif flow.ingestion_status == "RUNNING":
            stages = {
                "READING": "Đang đọc tệp nguồn",
                "VALIDATING": "Đang kiểm tra dữ liệu đầu vào",
                "RECONCILING": "Đang đối soát",
                "WRITING": "Đang ghi Bronze",
                "COMMITTING": "Đang xác nhận lưu Bronze",
            }
            bronze.state, bronze.detail = (
                "running",
                stages.get(str(row.get("ingestion_stage")), "Đang nạp dữ liệu"),
            )
            bronze.at = row.get("bronze_started_at")
        else:
            bronze.state, bronze.detail = (
                "waiting",
                "Chờ nạp Bronze" if flow.ingestion_status == "QUEUED" else "Chờ thử lại nạp Bronze",
            )
        return flow
    bronze.state, bronze.detail, bronze.at = (
        "done",
        "Đã lưu Bronze thành công",
        row.get("bronze_finished_at"),
    )
    if not flow.auto_job_id and not flow.run_id:
        queue.state = (
            "blocked"
            if row.get("paused") or not automatic_enabled or row.get("dataset_status") != "ACTIVE"
            else "waiting"
        )
        queue.detail = (
            "Bộ dữ liệu đang tạm dừng tự xử lý"
            if row.get("paused")
            else "Hệ thống tự động chưa được cập nhật"
            if not automatic_enabled
            else "Bộ dữ liệu không hoạt động"
            if row.get("dataset_status") != "ACTIVE"
            else "Chưa ghi nhận lượt Silver; cần kiểm tra cấu hình và lần nạp"
        )
        return flow
    status = flow.silver_status
    if not flow.auto_job_id:
        # Một số file lịch sử được xử lý trực tiếp, không có bản ghi auto job.
        # Dùng trạng thái cleaning thực tế; không tạo giả một auto job.
        publication_status = row.get("publication_status")
        status = (
            "FAILED"
            if row.get("cleaning_status") == "FAILED" or publication_status == "FAILED"
            else "SUCCESS"
            if publication_status in {"PUBLISHED", "PUBLISHED_WITH_QUARANTINE"}
            else "QUARANTINED"
            if publication_status in {"BLOCKED", "SKIPPED"}
            else "RUNNING"
        )
    queue.at = row.get("silver_created_at")
    if status == "QUEUED":
        queue.state, queue.detail = "waiting", "Đã vào hàng đợi, chờ bộ xử lý Silver"
        return flow
    queue.state, queue.detail = "done", "Đã nhận lượt xử lý Silver"
    if not flow.auto_job_id:
        queue.detail = "Đã ghi nhận lượt xử lý Silver trực tiếp trước đây"
        queue.at = row.get("cleaning_started_at")
    if status == "CANCELLED":
        if flow.attempt_number == 0:
            queue.state, queue.detail = "blocked", "Lượt tự xử lý đã dừng trước khi nhận việc"
        else:
            cleaning.state, cleaning.detail = "blocked", "Lượt tự xử lý đã dừng"
        return flow
    completed = row.get("cleaning_status") == "COMPLETED" or status in {
        "SUCCESS",
        "QUARANTINED",
    }
    if completed:
        cleaning.state, cleaning.detail = "done", "Đã làm sạch, chuẩn hóa và kiểm tra chất lượng"
        # DB không lưu thời điểm kết thúc riêng của cleaning; không gán giờ công bố cho bước này.
    elif row.get("cleaning_status") == "FAILED":
        cleaning.state, cleaning.detail = "failed", "Làm sạch hoặc kiểm tra chất lượng thất bại"
        return flow
    elif status == "RUNNING":
        cleaning.state, cleaning.detail = (
            "running",
            "Đang nhận diện, làm sạch, chuẩn hóa và kiểm tra chất lượng",
        )
        cleaning.at = row.get("cleaning_started_at") or row.get("attempt_started_at")
    else:
        cleaning.state = "waiting" if status == "RETRY_WAIT" else "failed"
        cleaning.detail = (
            "Xử lý gặp lỗi, đang chờ thử lại"
            if cleaning.state == "waiting"
            else "Không hoàn tất xử lý Silver"
        )
        return flow
    published = row.get("publication_status") in {"PUBLISHED", "PUBLISHED_WITH_QUARANTINE"} or (
        row.get("result") or {}
    ).get("status") in {"PUBLISHED", "PUBLISHED_WITH_QUARANTINE"}
    if published and flow.snapshot_id:
        publication.state, publication.detail = "done", "Đã công bố phiên bản Silver"
        if flow.records_quarantined:
            publication.detail = "Đã công bố phần đạt yêu cầu; các dòng lỗi được cách ly"
        publication.at = row.get("silver_finished_at") or row.get("cleaning_finished_at")
    elif status == "QUARANTINED" or row.get("publication_status") in {
        "BLOCKED",
        "SKIPPED",
    }:
        publication.state = "blocked"
        publication.detail = (
            "Lượt này chỉ làm sạch, không công bố Silver; xem báo cáo"
            if row.get("publication_status") == "SKIPPED"
            else "Chất lượng chưa đạt điều kiện công bố; xem báo cáo và hồ sơ cách ly"
        )
    elif status in {"FAILED", "RETRY_WAIT"}:
        publication.state = "waiting" if status == "RETRY_WAIT" else "failed"
        publication.detail = (
            "Công bố gặp lỗi; chờ thử lại"
            if publication.state == "waiting"
            else "Công bố Silver thất bại"
        )
    elif row.get("publication_status") == "FAILED":
        publication.state, publication.detail = "failed", "Công bố Silver thất bại"
    elif row.get("publication_status") == "RUNNING":
        publication.state, publication.detail = "running", "Đang công bố Silver"
    elif status == "SUCCESS":
        publication.state, publication.detail = (
            "blocked",
            "Chưa ghi nhận bằng chứng phiên bản Silver; cần kiểm tra kết quả",
        )
    return flow
