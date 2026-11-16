"""Kiểm tra báo cáo cuối cùng và phân loại ô trống hợp lệ, lỗi và cảnh báo."""

from __future__ import annotations

import pytest

from app.modules.pipeline.services.cleaning_report import final_cleaning_report, issue_level


def test_published_report_uses_history_without_rewriting_original():
    stored = {
        "status": "CLEANED",
        "publication": None,
        "issues": [{"code": "NULL_VALUE_PRESERVED", "severity": "INFO", "disposition": "PRESERVED"}]
        * 99,
    }
    run = {
        "run_id": "run-1",
        "cleaning_status": "COMPLETED",
        "publication_status": "PUBLISHED",
        "records_read": 20000,
        "records_passed": 20000,
        "records_quarantined": 0,
        "records_published": 20000,
        "silver_snapshot_id": "snapshot-1",
    }
    report = final_cleaning_report(stored, run)
    assert report["status"] == "PUBLISHED"
    assert report["publication"]["snapshot_id"] == "snapshot-1"
    assert report["publication"]["status"] == "PUBLISHED"
    assert report["issue_summary"] == {"error": 0, "warning": 0, "information": 99}
    assert stored["status"] == "CLEANED"
    assert stored["publication"] is None


@pytest.mark.parametrize(
    ("issue", "level"),
    [
        ({"severity": "error"}, "error"),
        ({"severity": "INFO", "disposition": "QUARANTINED"}, "error"),
        ({"severity": "warning"}, "warning"),
        ({"disposition": "COLLAPSED"}, "information"),
        ({"disposition": "NORMALIZED"}, "information"),
        ({}, "warning"),
    ],
)
def test_classification_does_not_confuse_processing_notes_with_errors(issue, level):
    assert issue_level(issue) == level
