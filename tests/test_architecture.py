from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.deps import get_current_user, require_role
from app.core.exceptions import AppException
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.infra.duckdb import DuckDBConnectionSettings
from app.infra.llm import MockLLMClient
from app.infra.minio import MinioClientSettings
from app.main import app
from app.modules.dashboard.schemas import KpiRead
from app.modules.datasources.schemas import DataSourceRead
from app.modules.ingestion.schemas import IngestionJobRead
from app.modules.nlq.intent_parser import parse_intent
from app.modules.nlq.schema_retriever import retrieve_schema_context
from app.modules.nlq.sql_generator import generate_sql
from app.modules.nlq.sql_validator import validate_read_only_sql
from app.modules.query_history.schemas import QueryHistoryRead
from app.modules.users.schemas import UserRead


def test_request_id_middleware_sets_header() -> None:
    client = TestClient(app)

    response = client.get("/health", headers={"X-Request-ID": "req-test"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "req-test"


def test_security_password_and_token_helpers() -> None:
    password_hash = hash_password("secret")
    token = create_access_token(
        "user-1", expires_delta=timedelta(minutes=5), claims={"roles": ["admin"]}
    )

    assert verify_password("secret", password_hash)
    assert not verify_password("wrong", password_hash)
    assert decode_access_token(token)["sub"] == "user-1"


@pytest.mark.asyncio
async def test_auth_dependencies_resolve_user_and_roles() -> None:
    token = create_access_token("user-1", claims={"roles": ["admin"]})
    user = await get_current_user(f"Bearer {token}")
    role_dependency = require_role("admin")

    assert user["sub"] == "user-1"
    assert role_dependency(user)["roles"] == ["admin"]


@pytest.mark.asyncio
async def test_auth_dependencies_reject_missing_or_wrong_role() -> None:
    with pytest.raises(AppException):
        await get_current_user(None)

    role_dependency = require_role("admin")
    with pytest.raises(AppException):
        role_dependency({"roles": ["viewer"]})


@pytest.mark.asyncio
async def test_nlq_flow_helpers_and_endpoint() -> None:
    client = TestClient(app)

    assert parse_intent("Tổng số sinh viên là bao nhiêu?") == "aggregate"
    assert parse_intent("Danh sách sinh viên") == "lookup"
    schema_context = retrieve_schema_context("lookup")
    assert "Intent: lookup" in schema_context
    assert "Gold dataset demo_student_summary" in schema_context
    assert validate_read_only_sql("select 1") == "select 1"
    with pytest.raises(AppException):
        validate_read_only_sql("drop table users")

    generated = await generate_sql("question", "schema", MockLLMClient())
    response = client.post("/api/v1/nlq/ask", json={"question": "Danh sách sinh viên"})

    assert generated.endswith("SQL:")
    assert response.status_code == 200
    assert response.json() == {"sql": "select 1", "rows": []}


def test_architecture_settings_and_schemas() -> None:
    duckdb_settings = DuckDBConnectionSettings()
    minio_settings = MinioClientSettings()

    assert duckdb_settings.database == ":memory:"
    assert minio_settings.endpoint
    assert UserRead(id=1, email="a@example.com").roles == []
    assert DataSourceRead(id=1, name="SIS", source_type="postgres").name == "SIS"
    job = IngestionJobRead(
        ingestion_job_id=1,
        data_source_id=1,
        dataset_id=1,
        status="QUEUED",
        stage="QUEUED",
        source_table="input.csv",
        created_at=None,
        started_at=None,
        finished_at=None,
        requested_by="1",
        rows_read=None,
        rows_written=None,
        rows_rejected=None,
        error_message=None,
        output=None,
    )
    assert job.status == "QUEUED"
    assert KpiRead(key="students", value=100).unit is None
    assert (
        QueryHistoryRead.model_validate(
            {"id": 1, "question": "Q", "created_at": "2026-09-16T00:00:00"}
        ).question
        == "Q"
    )
