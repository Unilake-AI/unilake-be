"""Ghép báo cáo kiểm tra với kết quả cuối cùng, phân biệt lỗi và ghi nhận xử lý hợp lệ."""

from __future__ import annotations

from collections import Counter
from typing import Any


def issue_level(issue: dict[str, Any]) -> str:
    severity = str(issue.get("severity", "")).upper()
    disposition = str(issue.get("disposition", "")).upper()
    if severity in {"ERROR", "CRITICAL"} or disposition == "QUARANTINED":
        return "error"
    if severity in {"WARNING", "WARN"}:
        return "warning"
    if severity == "INFO" or disposition in {"PRESERVED", "NORMALIZED", "COLLAPSED"}:
        return "information"
    return "warning"


def final_cleaning_report(stored: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    """File kiểm tra giữ nguyên; trạng thái công bố được lấy từ lịch sử đã xác nhận trong DB."""
    publication_status = run["publication_status"]
    statuses = {
        "PUBLISHED": "PUBLISHED",
        "PUBLISHED_WITH_QUARANTINE": "PUBLISHED_WITH_QUARANTINE",
        "BLOCKED": "QUARANTINED",
        "FAILED": "FAILED",
        "SKIPPED": "EMPTY",
    }
    status = statuses.get(publication_status)
    if status is None:
        status = {"COMPLETED": "CLEANED", "FAILED": "FAILED"}.get(run["cleaning_status"], "RUNNING")
    issues = stored.get("issues", [])
    if not isinstance(issues, list) or any(not isinstance(issue, dict) for issue in issues):
        raise ValueError("INVALID_CLEANING_REPORT")
    counts = Counter(issue_level(issue) for issue in issues)
    publication = None
    if run.get("silver_snapshot_id"):
        publication = {
            **(stored.get("publication") or {}),
            "status": publication_status,
            "snapshot_id": run["silver_snapshot_id"],
            "records_published": run["records_published"],
        }
    report = {
        **stored,
        "status": status,
        "cleaning_status": run["cleaning_status"],
        "publication_status": publication_status,
        "publication": publication,
        "issues": issues,
        "issue_summary": {name: counts[name] for name in ("error", "warning", "information")},
        "report_note": "Thông tin mức ghi nhận không phải lỗi dữ liệu. Trạng thái xuất bản lấy từ lịch sử xử lý hiện tại.",
    }
    for name in (
        "run_id",
        "ingestion_job_id",
        "dataset_id",
        "domain",
        "entity",
        "requested_by",
        "bronze_version",
        "input_version",
        "records_read",
        "records_passed",
        "records_quarantined",
        "records_collapsed",
        "records_published",
        "silver_snapshot_id",
        "error_code",
        "started_at",
        "finished_at",
    ):
        if name in run:
            report[name] = run[name]
    return report
