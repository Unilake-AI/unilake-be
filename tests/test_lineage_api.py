from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Table, create_engine, insert
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.roles import RoleCode
from app.core.security import create_access_token
from app.dependencies import get_db
from app.main import create_app
from app.modules.auth.models import LoginSession, Role, UserRole
from app.modules.governance.models import PipelineLineage
from app.modules.pipeline.lineage import record_pipeline_lineage_sync
from app.modules.users.models import User

LINEAGE_ENDPOINT = "/api/v1/governance/lineage"


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        execution_options={"schema_translate_map": {"app": None}},
    )
    cast("Table", PipelineLineage.__table__).create(eng)
    cast("Table", Role.__table__).create(eng)
    cast("Table", User.__table__).create(eng)
    cast("Table", UserRole.__table__).create(eng)
    cast("Table", LoginSession.__table__).create(eng)
    with Session(eng) as session, session.begin():
        for idx, role_code in enumerate(RoleCode, start=1):
            session.add(Role(role_id=idx, role_code=role_code.value, role_name=role_code.value))
            session.add(
                User(
                    app_user_id=idx,
                    username=f"user_{role_code.value.lower()}",
                    password_hash="hash",
                    is_active=True,
                )
            )
            session.add(UserRole(app_user_id=idx, role_id=idx))
            session.add(
                LoginSession(login_session_id=idx, app_user_id=idx, session_status="ACTIVE")
            )
    yield eng
    eng.dispose()


@pytest.fixture
def client(engine) -> Iterator[TestClient]:
    application = create_app()

    def override_db() -> Iterator[MagicMock]:
        with Session(engine) as session, session.begin():
            db = MagicMock()
            db.scalars = AsyncMock(side_effect=session.scalars)
            db.execute = AsyncMock(side_effect=session.execute)
            yield db

    application.dependency_overrides[get_db] = override_db
    with TestClient(application) as api:
        yield api


_ROLE_USER_IDS = {role.value: str(idx) for idx, role in enumerate(RoleCode, start=1)}


def headers(role: str = "DATA_ADMIN") -> dict[str, str]:
    user_id = _ROLE_USER_IDS.get(role, "1")
    token = create_access_token(
        user_id, claims={"roles": [role] if role else [], "sid": int(user_id)}
    )
    return {"Authorization": f"Bearer {token}"}


def test_lineage_requires_authentication(client: TestClient) -> None:
    response = client.get(LINEAGE_ENDPOINT)
    assert response.status_code == 401


def test_lineage_requires_data_admin_role(client: TestClient) -> None:
    for role in ["VIEWER", "ADMISSIONS_MANAGER", "ACADEMIC_STAFF", "HR_MANAGER"]:
        response = client.get(LINEAGE_ENDPOINT, headers=headers(role))
        assert response.status_code == 403


def test_lineage_empty_graph(client: TestClient) -> None:
    response = client.get(LINEAGE_ENDPOINT, headers=headers("DATA_ADMIN"))
    assert response.status_code == 200
    data = response.json()
    assert data["nodes"] == []
    assert data["edges"] == []


def test_lineage_two_stage_dag(client: TestClient, engine) -> None:
    # Chặng 1: Source -> Bronze
    with Session(engine) as session, session.begin():
        session.execute(
            insert(PipelineLineage).values(
                source_layer="SOURCE",
                source_name="01-admissions.xlsx",
                target_layer="BRONZE",
                target_name="s3://bronze/admissions/2026-11-06/job-1/01-admissions.xlsx",
                lineage_metadata={
                    "ingestion_job_id": 1,
                    "domain": "admissions",
                    "records_read": 100,
                    "records_passed": 100,
                    "records_rejected": 0,
                },
                created_at=datetime.now(UTC),
            )
        )
        # Chặng 2: Bronze -> Silver
        session.execute(
            insert(PipelineLineage).values(
                source_layer="BRONZE",
                source_name="s3://bronze/admissions/2026-11-06/job-1/01-admissions.xlsx",
                target_layer="SILVER",
                target_name="applicant",
                lineage_metadata={
                    "cleaning_run_id": "clean-uuid-1",
                    "silver_snapshot_id": "snap-uuid-1",
                    "domain": "admissions",
                    "entity": "applicant",
                    "records_read": 100,
                    "records_passed": 95,
                    "records_quarantined": 5,
                    "records_collapsed": 0,
                },
                created_at=datetime.now(UTC),
            )
        )

    response = client.get(LINEAGE_ENDPOINT, headers=headers("DATA_ADMIN"))
    assert response.status_code == 200
    graph = response.json()

    # 3 nodes: SOURCE -> BRONZE -> SILVER
    nodes = {node["id"]: node for node in graph["nodes"]}
    assert len(nodes) == 3
    assert "SOURCE:01-admissions.xlsx" in nodes
    assert "BRONZE:s3://bronze/admissions/2026-11-06/job-1/01-admissions.xlsx" in nodes
    assert "SILVER:applicant" in nodes

    bronze_node = nodes["BRONZE:s3://bronze/admissions/2026-11-06/job-1/01-admissions.xlsx"]
    assert bronze_node["layer"] == "BRONZE"
    assert bronze_node["label"] == "01-admissions.xlsx"

    # 2 edges connecting the nodes
    edges = graph["edges"]
    assert len(edges) == 2

    edge1 = edges[0]
    assert edge1["source"] == "SOURCE:01-admissions.xlsx"
    assert edge1["target"] == "BRONZE:s3://bronze/admissions/2026-11-06/job-1/01-admissions.xlsx"
    assert edge1["metadata"]["records_read"] == 100
    assert edge1["metadata"]["records_passed"] == 100

    edge2 = edges[1]
    assert edge2["source"] == "BRONZE:s3://bronze/admissions/2026-11-06/job-1/01-admissions.xlsx"
    assert edge2["target"] == "SILVER:applicant"
    assert edge2["metadata"]["records_quarantined"] == 5
    assert edge2["metadata"]["records_passed"] == 95


def test_lineage_filter_by_domain(client: TestClient, engine) -> None:
    with Session(engine) as session, session.begin():
        session.execute(
            insert(PipelineLineage).values(
                source_layer="SOURCE",
                source_name="admissions.csv",
                target_layer="BRONZE",
                target_name="s3://bronze/admissions.csv",
                lineage_metadata={"domain": "admissions"},
                created_at=datetime.now(UTC),
            )
        )
        session.execute(
            insert(PipelineLineage).values(
                source_layer="SOURCE",
                source_name="hr.csv",
                target_layer="BRONZE",
                target_name="s3://bronze/hr.csv",
                lineage_metadata={"domain": "hr"},
                created_at=datetime.now(UTC),
            )
        )

    # Filter admissions
    resp = client.get(
        LINEAGE_ENDPOINT, params={"domain_id": "admissions"}, headers=headers("DATA_ADMIN")
    )
    assert resp.status_code == 200
    admissions_graph = resp.json()
    assert len(admissions_graph["edges"]) == 1
    assert admissions_graph["edges"][0]["source"] == "SOURCE:admissions.csv"

    # Filter hr
    resp = client.get(LINEAGE_ENDPOINT, params={"domain_id": "hr"}, headers=headers("DATA_ADMIN"))
    assert resp.status_code == 200
    hr_graph = resp.json()
    assert len(hr_graph["edges"]) == 1
    assert hr_graph["edges"][0]["source"] == "SOURCE:hr.csv"


def test_record_pipeline_lineage_sync(engine) -> None:
    record_pipeline_lineage_sync(
        engine,
        source_layer="BRONZE",
        source_name="s3://bronze/hr/employee.parquet",
        target_layer="SILVER",
        target_name="employee",
        metadata={"domain": "hr", "records_passed": 50},
    )

    with Session(engine) as session:
        rows = session.query(PipelineLineage).all()
        assert len(rows) == 1
        assert rows[0].source_layer == "BRONZE"
        assert rows[0].target_name == "employee"
        assert rows[0].lineage_metadata["records_passed"] == 50
