"""Kiểm tra phân loại lỗi và quyền API tự chạy Silver, không cần hạ tầng thật."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from minio.error import S3Error
from sqlalchemy.exc import DBAPIError
from urllib3.response import HTTPResponse

from app.core.deps import get_current_user
from app.main import app
from app.modules.pipeline.services.silver_worker import classify_failure


@pytest.mark.parametrize(
    ("error", "retryable"),
    [
        (TimeoutError(), True),
        (ConnectionError(), True),
        (ValueError("invalid data"), False),
        (ValueError("ACTIVE_SNAPSHOT_CHANGED"), True),
        (RuntimeError("secret"), False),
    ],
)
def test_retry_only_for_transient_failures(error, retryable):
    code, can_retry = classify_failure(error)
    assert can_retry is retryable
    assert "secret" not in code


@pytest.mark.parametrize(
    ("state", "retryable"),
    [("23503", False), ("42501", False), ("42P01", False), ("40001", True), ("08006", True)],
)
def test_database_permission_and_data_failures_are_not_retried(state, retryable):
    class DatabaseFailureError(Exception):
        sqlstate = state

    error = DBAPIError("statement", {}, DatabaseFailureError("secret"))
    code, actual = classify_failure(error)
    assert actual is retryable
    assert "secret" not in code


@pytest.mark.parametrize(
    ("storage_code", "retryable"),
    [("AccessDenied", False), ("NoSuchBucket", False), ("InternalError", True)],
)
def test_storage_access_and_missing_inputs_are_not_retried(storage_code, retryable):
    error = S3Error(
        response=HTTPResponse(
            status={"AccessDenied": 403, "NoSuchBucket": 404}.get(storage_code, 500), body=b""
        ),
        code=storage_code,
        message="secret",
        resource="test",
        request_id="request",
        host_id="host",
    )
    code, actual = classify_failure(error)
    assert actual is retryable
    assert "secret" not in code


@pytest.mark.parametrize("role", ["VIEWER", "HR_STAFF", "ACADEMIC_STAFF", "ADMISSIONS_STAFF"])
def test_staff_cannot_enable_auto_pipeline(role):
    app.dependency_overrides[get_current_user] = lambda: {"sub": "14", "roles": [role]}
    try:
        response = TestClient(app).put(
            "/api/v1/pipeline/auto/datasets/1",
            json={"expected_revision": 0, "profile_id": "hr", "enabled": True},
        )
        assert response.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)
