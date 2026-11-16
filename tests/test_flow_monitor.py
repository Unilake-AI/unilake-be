"""Theo dõi chính xác bằng chứng từng bước, kể cả thất bại và thử lại."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.modules.pipeline.services.flow_monitor import project_flow

NOW = datetime(2026, 11, 16, tzinfo=UTC)


def flow(**changes):
    return project_flow(
        {
            "ingestion_job_id": 1,
            "dataset_id": 2,
            "dataset_name": "Nhân sự",
            "domain": "hr",
            "filename": "staff.csv",
            "uploaded_by": "hr_staff",
            "created_at": NOW,
            "ingestion_status": "SUCCESS",
            "bronze_rows": 10,
            "auto_job_id": "silver-1",
            "silver_status": "RUNNING",
            "dataset_status": "ACTIVE",
            **changes,
        },
        True,
    )


def states(result):
    return [s.state for s in result.steps]


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("QUEUED", "waiting"),
        ("RUNNING", "running"),
        ("FAILED", "failed"),
        ("RETRY_WAIT", "waiting"),
    ],
)
def test_before_bronze_never_claims_silver_started(status, expected):
    result = flow(ingestion_status=status, auto_job_id=None, silver_status=None)
    assert states(result) == ["done", expected, "pending", "pending", "pending"]


def test_bronze_written_is_not_silver_publication():
    result = flow(silver_status="QUEUED")
    assert states(result) == ["done", "done", "waiting", "pending", "pending"]
    assert result.snapshot_id is None


@pytest.mark.parametrize(("paused", "expected"), [(True, "blocked"), (False, "waiting")])
def test_missing_auto_job_and_paused_policy(paused, expected):
    result = flow(auto_job_id=None, silver_status=None, paused=paused)
    assert result.steps[2].state == expected
    assert result.steps[3].state == "pending"


def test_actual_cleaning_and_publication_are_separate():
    assert states(flow(cleaning_status="RUNNING")) == ["done", "done", "done", "running", "pending"]
    assert states(flow(cleaning_status="COMPLETED", publication_status="RUNNING")) == [
        "done",
        "done",
        "done",
        "done",
        "running",
    ]


@pytest.mark.parametrize(("status", "expected"), [("FAILED", "failed"), ("RETRY_WAIT", "waiting")])
def test_failure_placed_at_actual_phase(status, expected):
    assert flow(silver_status=status).steps[3].state == expected
    result = flow(
        silver_status=status, cleaning_status="COMPLETED", error_code="STORAGE_UNAVAILABLE"
    )
    assert result.steps[3].state == "done"
    assert result.steps[4].state == expected
    assert result.error_code == "STORAGE_UNAVAILABLE"


def test_quarantine_is_quality_checked_but_unpublished():
    result = flow(
        silver_status="QUARANTINED",
        cleaning_status="COMPLETED",
        publication_status="BLOCKED",
        records_quarantined=2,
    )
    assert states(result) == ["done", "done", "done", "done", "blocked"]
    assert result.snapshot_id is None


def test_success_requires_published_snapshot_evidence():
    assert flow(silver_status="SUCCESS").steps[4].state == "blocked"
    result = flow(
        silver_status="SUCCESS",
        snapshot_id="snapshot-1",
        result={
            "status": "PUBLISHED",
            "records_read": 10,
            "records_passed": 10,
            "records_quarantined": 0,
            "publication": {"records_published": 10},
        },
    )
    assert states(result) == ["done"] * 5
    assert result.records_read == result.records_published == 10
    # Không giả thời gian riêng cho bước cleaning khi DB chưa ghi nhận.
    assert result.steps[3].at is None


def test_cancelled_is_not_a_failure_or_success():
    result = flow(silver_status="CANCELLED", attempt_number=1)
    assert result.steps[3].state == "blocked"
    assert result.steps[4].state == "pending"


def test_new_retry_waits_even_if_prior_cleaning_completed():
    result = flow(silver_status="QUEUED", cleaning_status="COMPLETED", publication_status="FAILED")
    assert states(result) == ["done", "done", "waiting", "pending", "pending"]


def test_cancel_before_claim_does_not_claim_worker_started():
    result = flow(silver_status="CANCELLED", attempt_number=0)
    assert states(result) == ["done", "done", "blocked", "pending", "pending"]


def test_persisted_phase_error_visible_before_job_finalizes():
    assert flow(cleaning_status="FAILED").steps[3].state == "failed"
    assert flow(cleaning_status="COMPLETED", publication_status="FAILED").steps[4].state == "failed"


def test_historical_direct_cleaning_not_mislabeled_as_unprocessed():
    result = flow(
        auto_job_id=None,
        silver_status=None,
        run_id="manual-1",
        cleaning_status="COMPLETED",
        publication_status="PUBLISHED",
        snapshot_id="silver-1",
    )
    assert states(result) == ["done"] * 5
    assert result.auto_job_id is None
    assert "trực tiếp" in result.steps[2].detail


def test_cleaning_only_run_never_claims_publication_or_quality_failure():
    result = flow(
        auto_job_id=None,
        silver_status=None,
        run_id="preview-1",
        cleaning_status="COMPLETED",
        publication_status="SKIPPED",
    )
    assert result.steps[4].state == "blocked"
    assert "chỉ làm sạch" in result.steps[4].detail
