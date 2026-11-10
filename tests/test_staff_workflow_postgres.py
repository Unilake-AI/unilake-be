"""Kiểm tra luồng STAFF bằng HTTP và PostgreSQL trong vùng dữ liệu kiểm thử tách biệt."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import URL, Table, create_engine, event, insert, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.bronze_capture import store_bronze
from app.core.config import settings
from app.core.deps import get_current_user
from app.core.exceptions import AppException, app_exception_handler
from app.dependencies import get_db
from app.infra.db.base import Base
from app.modules.auth.models import Role, UserRole
from app.modules.datasources.datasets_router import router as datasets_router
from app.modules.datasources.models import DataLayer, Dataset, DataSource, SourceSystem
from app.modules.ingestion import upload_capture, uploads
from app.modules.ingestion.execution import IngestionExecutionService
from app.modules.ingestion.models import IngestionJob
from app.modules.ingestion.router import router as ingestion_router
from app.modules.ingestion.upload_capture import UploadCapture
from app.modules.pipeline import data_quality_issues_router as issues
from app.modules.pipeline.models import (
    CLEANING_RUNS,
    DATA_QUALITY_ISSUE_EVENTS,
    DATA_QUALITY_ISSUE_RECORDS,
    DATA_QUALITY_ISSUES,
    PIPELINE_LINEAGE,
    QUARANTINE_RECORDS,
)
from app.modules.pipeline.router import router as pipeline_router
from app.modules.staff.router import router as staff_router
from app.modules.users.models import Assignment, Department, Employee, Position, User
from app.repositories.ingestion_repo import IngestionRepository
from tests.test_ingestion_uploads import MemoryStore

pytestmark = pytest.mark.skipif(
    os.getenv("STAFF_API_INTEGRATION_TEST") != "1",
    reason="Requires isolated PostgreSQL test schema",
)


@pytest.fixture(scope="module")
def database_engine():
    schema = "staff_test_" + uuid4().hex
    engine = create_engine(
        URL.create(
            "postgresql+psycopg",
            username=settings.db_user,
            password=settings.db_password,
            host=settings.db_host,
            port=settings.db_port,
            database=settings.db_name,
        ),
        execution_options={"schema_translate_map": {"app": schema}},
    )

    # Cả câu lệnh viết tay cũng phải ở vùng kiểm thử, không truy cập bảng đang vận hành.
    @event.listens_for(engine, "before_cursor_execute", retval=True)
    def route_test_schema(_conn, _cursor, statement, parameters, _context, _many):
        return statement.replace("app.", f'"{schema}".'), parameters

    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        models = (
            Role,
            UserRole,
            Department,
            Employee,
            Position,
            Assignment,
            User,
            DataLayer,
            SourceSystem,
            Dataset,
            DataSource,
            IngestionJob,
        )
        tables = [cast("Table", model.__table__) for model in models]
        tables += [
            CLEANING_RUNS,
            QUARANTINE_RECORDS,
            DATA_QUALITY_ISSUES,
            DATA_QUALITY_ISSUE_RECORDS,
            DATA_QUALITY_ISSUE_EVENTS,
        ]
        Base.metadata.create_all(engine, tables=tables)
        PIPELINE_LINEAGE.create(engine)
        yield engine
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


def asynchronous(session):
    result = SimpleNamespace(add=session.add)
    for name in ("scalar", "scalars", "execute", "get", "commit", "rollback", "flush"):
        setattr(result, name, AsyncMock(side_effect=getattr(session, name)))
    return cast("AsyncSession", result)


@pytest.fixture
def environment(database_engine, monkeypatch):
    connection = database_engine.connect()
    transaction = connection.begin()
    now = datetime.now(UTC)
    run_id, record_id = str(uuid4()), str(uuid4())
    with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
        session.add_all(
            [
                Department(
                    department_id=7,
                    department_code="HR",
                    department_name="Nhân sự",
                    department_type="OFFICE",
                    status="ACTIVE",
                ),
                Department(
                    department_id=8,
                    department_code="AC",
                    department_name="Đào tạo",
                    department_type="OFFICE",
                    status="ACTIVE",
                ),
                Department(
                    department_id=9,
                    department_code="HR2",
                    department_name="Đơn vị khác",
                    department_type="OFFICE",
                    status="ACTIVE",
                ),
                Position(position_id=9, position_code="STAFF", position_name="Nhân viên"),
                Role(role_id=1, role_code="DATA_ADMIN", role_name="Quản trị dữ liệu"),
                Role(role_id=2, role_code="HR_STAFF", role_name="Nhân viên nhân sự"),
                Role(role_id=3, role_code="ACADEMIC_STAFF", role_name="Nhân viên đào tạo"),
                Role(role_id=4, role_code="VIEWER", role_name="Người xem"),
            ]
        )
        session.flush()
        for uid, dept, role in (
            (1, None, 1),
            (14, 7, 2),
            (15, 7, 2),
            (16, 9, 2),
            (17, 8, 3),
            (18, None, 2),
            (19, None, 4),
        ):
            session.add(
                Employee(
                    employee_id=uid,
                    employee_code=f"E{uid}",
                    full_name=f"Nhân viên {uid}",
                    employee_status="ACTIVE",
                )
            )
            session.flush()
            session.add(
                User(
                    app_user_id=uid,
                    username=f"user{uid}",
                    password_hash="unused-in-isolated-test",
                    employee_id=uid,
                    is_active=True,
                )
            )
            session.flush()
            session.add(UserRole(app_user_id=uid, role_id=role))
            if dept is not None:
                session.add(
                    Assignment(
                        employee_id=uid,
                        department_id=dept,
                        position_id=9,
                        start_date=date(2020, 1, 1),
                        assignment_status="ACTIVE",
                    )
                )
        session.add(DataLayer(data_layer_id=1, layer_code="BRONZE", layer_name="Bronze"))
        session.add(
            SourceSystem(
                source_system_id=9,
                source_code="HR",
                source_name="Nhân sự",
                source_type="FILE",
                is_active=True,
            )
        )
        session.flush()
        session.add(
            Dataset(
                dataset_id=9,
                dataset_code="HR_EMPLOYEES",
                dataset_name="Danh sách nhân viên",
                domain="hr",
                source_system_id=9,
                data_layer_id=1,
                owner_department_id=7,
                status="ACTIVE",
            )
        )
        session.flush()
        session.add(
            DataSource(
                data_source_id=9,
                source_system_id=9,
                source_name="UPLOAD_9_CSV",
                source_type="CSV",
                connection_type="UPLOAD",
                configuration={"upload_dataset_id": 9},
                is_active=True,
                ingestion_count=0,
            )
        )
        session.flush()
        session.add(
            IngestionJob(
                ingestion_job_id=100,
                data_source_id=9,
                dataset_id=9,
                target_layer_id=1,
                job_type="UPLOAD_V1",
                source_table="employees.csv",
                status="SUCCESS",
                stage="COMPLETED",
                requested_by="14",
                created_at=now.replace(tzinfo=None) - timedelta(days=1),
                rows_read=1,
                rows_written=1,
                output={
                    "source_type": "CSV",
                    "parser_options": {"delimiter": ",", "encoding": "utf-8", "has_header": True},
                },
            )
        )
        session.flush()
        session.execute(
            insert(CLEANING_RUNS).values(
                run_id=run_id,
                ingestion_job_id=100,
                dataset_id=9,
                domain="hr",
                entity="employee",
                requested_by="1",
                session="institution",
                bronze_version="old",
                cleaning_status="COMPLETED",
                publication_status="BLOCKED",
                records_published=0,
                started_at=now - timedelta(hours=1),
                finished_at=now - timedelta(minutes=50),
            )
        )
        session.execute(
            insert(QUARANTINE_RECORDS).values(
                record_id=record_id,
                cleaning_run_id=run_id,
                locator="csv:record:1",
                record_position=1,
                issues=[{"code": "MISSING_VALUE", "field": "full_name"}],
                error_codes=["MISSING_VALUE"],
            )
        )
        session.commit()

    async def database():
        with Session(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        ) as session:
            try:
                yield asynchronous(session)
                session.commit()
            except Exception:
                session.rollback()
                raise

    store = MemoryStore()
    monkeypatch.setattr(uploads, "upload_writer_client", lambda: store)
    monkeypatch.setattr(upload_capture, "upload_reader_client", lambda: store)
    monkeypatch.setattr("app.bronze_capture.bronze_writer_client", lambda: store)
    monkeypatch.setattr("app.bronze_capture.bronze_verifier_client", lambda: store)
    app = FastAPI()
    app.add_exception_handler(AppException, app_exception_handler)
    for router in (staff_router, datasets_router, ingestion_router, issues.router, pipeline_router):
        app.include_router(router)
    user = {"sub": "14", "roles": ["HR_STAFF"]}
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = database
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client,
            user=user,
            connection=connection,
            store=store,
            record_id=record_id,
            run_id=run_id,
        )
    transaction.rollback()
    connection.close()


def actor(environment, uid=14, role="HR_STAFF"):
    environment.user.update(sub=str(uid), roles=[role])


def create_issue(environment, assignee=14):
    actor(environment, 1, "DATA_ADMIN")
    response = environment.client.post(
        "/data-quality-issues",
        json={
            "title": "Sửa họ tên",
            "department_id": 7,
            "assignee_user_id": assignee,
            "quarantine_record_ids": [environment.record_id],
        },
    )
    assert response.status_code == 201, response.text
    actor(environment)
    return response.json()["issue_id"]


def upload(environment, issue_id, filename="employees.csv"):
    return environment.client.post(
        f"/data-quality-issues/{issue_id}/uploads",
        files={"file": (filename, b"employee_code,full_name\nE1,Nguyen An\n")},
    )


def process_upload(environment):
    async def run():
        with Session(
            bind=environment.connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        ) as session:
            repo = IngestionRepository(asynchronous(session))
            job = await repo.claim()
            assert job is not None
            await IngestionExecutionService(repo).execute(
                job, lambda: UploadCapture(job), store_bronze
            )
            return job.ingestion_job_id

    return asyncio.run(run())


def test_staff_context_explains_domains_departments_and_missing_assignment(environment):
    response = environment.client.get("/staff/context")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["default_domain"] == "hr"
    assert data["domains"] == [{"domain": "hr", "name": "Nhân sự"}]
    assert data["departments"][0]["department_id"] == 7
    assert data["can_resolve_issues"] is True
    assert data["can_manage_silver"] is False
    actor(environment, 18)
    data = environment.client.get("/staff/context").json()
    assert data["can_upload"] is True
    assert data["can_resolve_issues"] is False
    assert data["needs_department_assignment"] is True
    actor(environment, 19, "VIEWER")
    assert environment.client.get("/staff/context").status_code == 403


def test_full_staff_upload_fix_submit_and_admin_verify_with_real_postgres(environment):
    issue_id = create_issue(environment)
    detail = environment.client.get(f"/data-quality-issues/{issue_id}").json()
    assert detail["quarantine_records"][0]["source_table"] == "employees.csv"
    response = upload(environment, issue_id)
    assert response.status_code == 202, response.text
    assert response.json()["quality_issue_id"] == issue_id
    job_id = response.json()["ingestion_job_id"]
    waiting = environment.client.post(
        f"/data-quality-issues/{issue_id}/resolution",
        json={"ingestion_job_id": job_id, "note": "Đã sửa"},
    )
    assert waiting.status_code == 422
    assert process_upload(environment) == job_id
    response = environment.client.post(
        f"/data-quality-issues/{issue_id}/resolution",
        json={"ingestion_job_id": job_id, "note": "Đã sửa đầy đủ"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "PENDING_VERIFICATION"
    assert upload(environment, issue_id).status_code == 409
    verified_run = str(uuid4())
    with Session(bind=environment.connection, join_transaction_mode="create_savepoint") as session:
        session.execute(
            insert(CLEANING_RUNS).values(
                run_id=verified_run,
                ingestion_job_id=job_id,
                dataset_id=9,
                domain="hr",
                entity="employee",
                requested_by="1",
                session="institution",
                bronze_version="new",
                cleaning_status="COMPLETED",
                publication_status="PUBLISHED",
                records_published=1,
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
            )
        )
        session.commit()
    payload = {
        "cleaning_run_id": verified_run,
        "verified": True,
        "note": "Đã đối chiếu",
        "confirmed_record_ids": [environment.record_id],
    }
    assert (
        environment.client.post(
            f"/data-quality-issues/{issue_id}/verification", json=payload
        ).status_code
        == 403
    )
    actor(environment, 1, "DATA_ADMIN")
    verified = environment.client.post(
        f"/data-quality-issues/{issue_id}/verification", json=payload
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["status"] == "VERIFIED"
    assert [item["event_type"] for item in verified.json()["history"]] == [
        "ASSIGNED",
        "RESOLUTION_SUBMITTED",
        "VERIFIED",
    ]


@pytest.mark.parametrize(
    ("uid", "role", "status"),
    [(15, "HR_STAFF", 403), (16, "HR_STAFF", 404), (17, "ACADEMIC_STAFF", 404)],
)
def test_issue_upload_cannot_be_submitted_by_coworker_other_department_or_domain(
    environment, uid, role, status
):
    issue_id = create_issue(environment)
    actor(environment, uid, role)
    response = upload(environment, issue_id)
    assert response.status_code == status
    assert not environment.store.writes


def test_wrong_file_same_dataset_cannot_be_used_as_a_fix(environment):
    issue_id = create_issue(environment)
    assert upload(environment, issue_id, "attendance.csv").status_code == 422
    assert not environment.store.writes
    response = environment.client.post(
        "/ingestion/uploads",
        files={"file": ("attendance.csv", b"id\n1\n")},
        data={"dataset_id": "9"},
    )
    process_upload(environment)
    result = environment.client.post(
        f"/data-quality-issues/{issue_id}/resolution",
        json={"ingestion_job_id": response.json()["ingestion_job_id"], "note": "Sai loại file"},
    )
    assert result.status_code == 422


def test_issue_lists_and_stats_use_the_same_department_and_mine_filters(environment):
    first = create_issue(environment, 14)
    create_issue(environment, 15)
    assert environment.client.get("/data-quality-issues/stats").json()["total"] == 2
    own = environment.client.get("/data-quality-issues?mine_only=true").json()
    assert [item["issue_id"] for item in own] == [first]
    assert environment.client.get("/data-quality-issues/stats?mine_only=true").json()["total"] == 1
    actor(environment, 16)
    assert environment.client.get("/data-quality-issues").json() == []
    assert environment.client.get("/data-quality-issues/stats").json()["total"] == 0


def test_admin_can_choose_real_departments_and_only_eligible_assignees(environment):
    assert environment.client.get("/data-quality-issues/departments").status_code == 403
    actor(environment, 1, "DATA_ADMIN")
    assert len(environment.client.get("/data-quality-issues/departments").json()) == 3
    result = environment.client.get("/data-quality-issues/assignees?department_id=7&domain=hr")
    assert result.status_code == 200, result.text
    assert {row["user_id"] for row in result.json()} == {14, 15}


def test_dataset_detail_search_and_job_filters_remain_within_staff_domain(environment):
    assert environment.client.get("/datasets/9").status_code == 200
    assert len(environment.client.get("/datasets?search=nhân").json()) == 1
    assert environment.client.get("/datasets?search=%").json() == []
    assert (
        len(
            environment.client.get(
                "/ingestion/jobs?dataset_id=9&status=SUCCESS&mine_only=true"
            ).json()
        )
        == 1
    )
    assert environment.client.get("/ingestion/jobs?status=QUEUED").json() == []
    actor(environment, 17, "ACADEMIC_STAFF")
    assert environment.client.get("/datasets/9").status_code == 404
    assert environment.client.get("/datasets").json() == []
    assert environment.client.get("/ingestion/jobs?dataset_id=9").json() == []
    assert environment.client.get("/ingestion/jobs/stats").json()["total"] == 0


def test_staff_cannot_retry_or_run_cleaning(environment):
    assert environment.client.post("/ingestion/jobs/100/retry").status_code == 403
    assert (
        environment.client.post(
            "/pipeline/cleaning/ingestion-jobs/100", json={"profile_id": "employees"}
        ).status_code
        == 403
    )


@pytest.mark.parametrize("invalid", ["other-owner", "other-issue", "old-root"])
def test_successful_job_is_not_enough_to_submit_an_unrelated_file(environment, invalid):
    issue_id = create_issue(environment)
    if invalid == "other-owner":
        actor(environment, 15)
    response = environment.client.post(
        "/ingestion/uploads",
        files={"file": ("employees.csv", b"employee_code,full_name\nE1,Nguyen An\n")},
        data={"dataset_id": "9"},
    )
    job_id = response.json()["ingestion_job_id"]
    process_upload(environment)
    with Session(bind=environment.connection, join_transaction_mode="create_savepoint") as session:
        job = session.get(IngestionJob, job_id)
        assert job is not None
        if invalid == "other-issue":
            job.upload_input = {**(job.upload_input or {}), "quality_issue_id": str(uuid4())}
        elif invalid == "old-root":
            job.root_job_id = 100
        session.commit()
    actor(environment)
    result = environment.client.post(
        f"/data-quality-issues/{issue_id}/resolution",
        json={"ingestion_job_id": job_id, "note": "Không phải bản sửa hợp lệ"},
    )
    assert result.status_code == 422, result.text
    assert environment.client.get(f"/data-quality-issues/{issue_id}").json()["status"] == "ASSIGNED"


def test_retry_success_can_be_submitted_by_original_uploader(environment):
    issue_id = create_issue(environment)
    response = upload(environment, issue_id)
    job_id = response.json()["ingestion_job_id"]
    process_upload(environment)
    with Session(
        bind=environment.connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    ) as session:
        first = session.get(IngestionJob, job_id)
        assert first is not None
        retried = IngestionJob(
            data_source_id=9,
            dataset_id=9,
            target_layer_id=1,
            job_type="UPLOAD_V1",
            source_table="employees.csv",
            status="SUCCESS",
            stage="COMPLETED",
            requested_by="system-retry",
            created_at=datetime.now(UTC).replace(tzinfo=None),
            root_job_id=job_id,
            upload_input=first.upload_input,
        )
        session.add(retried)
        session.commit()
        retry_id = retried.ingestion_job_id
    result = environment.client.post(
        f"/data-quality-issues/{issue_id}/resolution",
        json={"ingestion_job_id": retry_id, "note": "File do tôi tải, hệ thống chạy lại"},
    )
    assert result.status_code == 200, result.text


def test_verification_rejection_requires_a_new_upload_and_preserves_history(environment):
    issue_id = create_issue(environment)
    first = upload(environment, issue_id).json()["ingestion_job_id"]
    process_upload(environment)
    path = f"/data-quality-issues/{issue_id}/resolution"
    assert (
        environment.client.post(
            path, json={"ingestion_job_id": first, "note": "Lần đầu"}
        ).status_code
        == 200
    )
    run_id = str(uuid4())
    with Session(bind=environment.connection, join_transaction_mode="create_savepoint") as session:
        session.execute(
            insert(CLEANING_RUNS).values(
                run_id=run_id,
                ingestion_job_id=first,
                dataset_id=9,
                domain="hr",
                entity="employee",
                requested_by="1",
                session="institution",
                bronze_version="new",
                cleaning_status="COMPLETED",
                publication_status="BLOCKED",
                records_published=0,
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
            )
        )
        session.commit()
    actor(environment, 1, "DATA_ADMIN")
    rejected = environment.client.post(
        f"/data-quality-issues/{issue_id}/verification",
        json={"cleaning_run_id": run_id, "verified": False, "note": "Vẫn còn lỗi"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "ASSIGNED"
    actor(environment)
    assert (
        environment.client.post(
            path, json={"ingestion_job_id": first, "note": "Nộp lại file cũ"}
        ).status_code
        == 422
    )
    second = upload(environment, issue_id).json()["ingestion_job_id"]
    process_upload(environment)
    accepted = environment.client.post(
        path, json={"ingestion_job_id": second, "note": "Bản sửa mới"}
    )
    assert accepted.status_code == 200, accepted.text
    assert len(accepted.json()["history"]) == 4


def test_expired_assignment_and_revoked_role_remove_issue_access(environment):
    issue_id = create_issue(environment)
    with Session(bind=environment.connection, join_transaction_mode="create_savepoint") as session:
        session.execute(
            update(Assignment).where(Assignment.employee_id == 14).values(end_date=date(2020, 1, 2))
        )
        session.commit()
    assert environment.client.get(f"/data-quality-issues/{issue_id}").status_code == 404
    assert environment.client.get("/staff/context").json()["needs_department_assignment"] is True
    with Session(bind=environment.connection, join_transaction_mode="create_savepoint") as session:
        session.query(UserRole).filter(UserRole.app_user_id == 14).delete()
        session.commit()
    assert environment.client.get("/staff/context").status_code == 403
    assert environment.client.get("/data-quality-issues").status_code == 403


def test_wrong_entity_cannot_be_verified_even_if_same_dataset_was_published(environment):
    issue_id = create_issue(environment)
    job_id = upload(environment, issue_id).json()["ingestion_job_id"]
    process_upload(environment)
    assert (
        environment.client.post(
            f"/data-quality-issues/{issue_id}/resolution",
            json={"ingestion_job_id": job_id, "note": "Đã sửa"},
        ).status_code
        == 200
    )
    run_id = str(uuid4())
    with Session(bind=environment.connection, join_transaction_mode="create_savepoint") as session:
        session.execute(
            insert(CLEANING_RUNS).values(
                run_id=run_id,
                ingestion_job_id=job_id,
                dataset_id=9,
                domain="hr",
                entity="attendance",
                requested_by="1",
                session="institution",
                bronze_version="new",
                cleaning_status="COMPLETED",
                publication_status="PUBLISHED",
                records_published=1,
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
            )
        )
        session.commit()
    actor(environment, 1, "DATA_ADMIN")
    response = environment.client.post(
        f"/data-quality-issues/{issue_id}/verification",
        json={
            "cleaning_run_id": run_id,
            "verified": True,
            "note": "Sai bảng dữ liệu",
            "confirmed_record_ids": [environment.record_id],
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_VERIFICATION_RUN"


def test_storage_failure_leaves_issue_assigned_and_no_new_job(environment, monkeypatch):
    issue_id = create_issue(environment)

    def unavailable(*_args, **_kwargs):
        raise OSError("Không được lộ cấu hình nội bộ")

    monkeypatch.setattr(uploads, "stage_file", unavailable)
    result = upload(environment, issue_id)
    assert result.status_code == 503
    assert "cấu hình nội bộ" not in result.text
    detail = environment.client.get(f"/data-quality-issues/{issue_id}").json()
    assert detail["status"] == "ASSIGNED"
    assert len(detail["history"]) == 1
    assert len(environment.client.get("/ingestion/jobs?dataset_id=9").json()) == 1


def test_invalid_replacement_file_cannot_move_issue_to_pending_verification(environment):
    issue_id = create_issue(environment)
    response = environment.client.post(
        f"/data-quality-issues/{issue_id}/uploads",
        files={"file": ("employees.csv", b"employee_code,full_name\nE1\n")},
    )
    assert response.status_code == 202
    job_id = response.json()["ingestion_job_id"]
    process_upload(environment)
    job = environment.client.get(f"/ingestion/jobs/{job_id}").json()
    assert job["status"] == "FAILED"
    assert job["validation_report"]["result"] == "FAIL"
    result = environment.client.post(
        f"/data-quality-issues/{issue_id}/resolution",
        json={"ingestion_job_id": job_id, "note": "Chưa được chấp nhận Bronze"},
    )
    assert result.status_code == 422
    assert environment.client.get(f"/data-quality-issues/{issue_id}").json()["status"] == "ASSIGNED"
