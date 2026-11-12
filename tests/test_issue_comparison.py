"""Kiểm thử đơn vị và tích hợp cho API đối chiếu dữ liệu trước - sau của Data Admin."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Table, create_engine, insert, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateTable

from app.core.config import settings
from app.core.roles import RoleCode
from app.core.security import create_access_token
from app.dependencies import get_db
from app.main import create_app
from app.modules.auth.models import LoginSession, Role, UserRole
from app.modules.datasources.models import DataLayer, Dataset, DataSource, SourceSystem
from app.modules.ingestion.models import IngestionJob
from app.modules.ingestion.readers.contract import ReadRequest
from app.modules.ingestion.readers.csv_reader import CSVReader
from app.modules.ingestion.readers.file_snapshot import read_file_snapshot
from app.modules.pipeline.cleaning import quarantine as quarantine_records
from app.modules.pipeline.models import (
    CLEANING_RUNS,
    DATA_QUALITY_ISSUE_EVENTS,
    DATA_QUALITY_ISSUE_RECORDS,
    DATA_QUALITY_ISSUES,
    QUARANTINE_RECORDS,
)
from app.modules.pipeline.services.issue_comparison import (
    build_issue_comparison,
    resolve_business_key_fields,
)
from app.modules.users.models import Assignment, Department, Employee, Position, User


class _FakeMinio:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str, str], tuple[bytes, str]] = {}

    def put(
        self,
        bucket: str,
        key: str,
        version_id: str,
        content: bytes,
        *,
        actual_version: str | None = None,
    ) -> None:
        self.objects[(bucket, key, version_id)] = (content, actual_version or version_id)

    def get_object(self, bucket: str, key: str, *, version_id: str) -> MagicMock:
        payload, actual_version = self.objects[(bucket, key, version_id)]
        response = MagicMock()
        response.headers = {"x-amz-version-id": actual_version}
        response.stream.return_value = [payload]
        return response


def _bronze_evidence(
    *,
    tmp_path,
    storage: _FakeMinio,
    filename: str,
    content: bytes,
    uri: str,
    version_id: str,
    actual_version: str | None = None,
    allowed_columns: list[str] | None = None,
) -> dict[str, Any]:
    subdir = tmp_path / version_id
    subdir.mkdir(parents=True, exist_ok=True)
    path = subdir / filename
    path.write_bytes(content)
    options = {"delimiter": ",", "encoding": "utf-8", "has_header": True}
    parsed = read_file_snapshot(
        ReadRequest("CSV", str(path), options, filename),
        CSVReader(),
        source_system_id="9",
        data_source_id="9",
        dataset_id="9",
    )
    assert parsed.provenance is not None
    key = uri.split(f"s3://{settings.minio_bucket_bronze}/", 1)[1]
    storage.put(
        settings.minio_bucket_bronze, key, version_id, content, actual_version=actual_version
    )
    evidence: dict[str, Any] = {
        "uri": uri,
        "version_id": version_id,
        "sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content),
        "source_type": "CSV",
        "file_name": filename,
        "parser_options": options,
        "parser_fingerprint": parsed.provenance.parser_fingerprint,
        "columns_fingerprint": parsed.provenance.columns_fingerprint,
        "source_system_id": 9,
        "data_source_id": 9,
        "dataset_id": 9,
    }
    if allowed_columns is not None:
        evidence["allowed_columns"] = allowed_columns
    return evidence


def _sqlite_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        execution_options={"schema_translate_map": {"app": None}},
    )
    tables = (
        cast("Table", Role.__table__),
        cast("Table", Department.__table__),
        cast("Table", Position.__table__),
        cast("Table", Employee.__table__),
        cast("Table", Assignment.__table__),
        cast("Table", User.__table__),
        cast("Table", UserRole.__table__),
        cast("Table", LoginSession.__table__),
        cast("Table", DataLayer.__table__),
        cast("Table", SourceSystem.__table__),
        cast("Table", Dataset.__table__),
        cast("Table", DataSource.__table__),
        cast("Table", IngestionJob.__table__),
        CLEANING_RUNS,
        QUARANTINE_RECORDS,
        DATA_QUALITY_ISSUES,
        DATA_QUALITY_ISSUE_RECORDS,
        DATA_QUALITY_ISSUE_EVENTS,
    )
    with engine.begin() as conn:
        for table in tables:
            ddl = str(CreateTable(table).compile(conn))
            ddl = ddl.replace("app.", "")
            ddl = ddl.replace("BIGINT GENERATED ALWAYS AS IDENTITY", "INTEGER")
            ddl = ddl.replace("BIGINT NOT NULL", "INTEGER NOT NULL")
            ddl = ddl.replace("'{}'::jsonb", "'{}'")
            ddl = ddl.replace("'[]'::jsonb", "'[]'")
            conn.execute(text(ddl))
    return engine


def _with_utc_datetimes(row: Any) -> dict[str, Any]:
    return {
        key: (
            value.replace(tzinfo=UTC)
            if isinstance(value, datetime) and value.tzinfo is None
            else value
        )
        for key, value in dict(row).items()
    }


def _async_db(session: Session) -> MagicMock:
    def _execute(statement: Any, params: Any = None) -> Any:
        result = (
            session.execute(statement, params) if params is not None else session.execute(statement)
        )
        orig_mappings = result.mappings

        def _mappings() -> Any:
            mapped = orig_mappings()
            orig_all = mapped.all
            orig_one_or_none = mapped.one_or_none
            orig_first = mapped.first
            orig_iter = mapped.__iter__

            class _WrappedMappings:
                def all(self) -> list[dict[str, Any]]:
                    return [_with_utc_datetimes(r) for r in orig_all()]

                def one_or_none(self) -> dict[str, Any] | None:
                    row = orig_one_or_none()
                    return _with_utc_datetimes(row) if row is not None else None

                def first(self) -> dict[str, Any] | None:
                    row = orig_first()
                    return _with_utc_datetimes(row) if row is not None else None

                def __iter__(self) -> Iterator[dict[str, Any]]:
                    return (_with_utc_datetimes(r) for r in orig_iter())

            return _WrappedMappings()

        result.mappings = _mappings
        return result

    db = MagicMock()
    db.execute = AsyncMock(side_effect=_execute)
    db.scalar = AsyncMock(side_effect=session.scalar)
    db.scalars = AsyncMock(side_effect=session.scalars)
    db.get = AsyncMock(side_effect=session.get)
    db.add = session.add
    db.flush = AsyncMock(side_effect=session.flush)
    db.commit = AsyncMock(side_effect=session.commit)
    db.rollback = AsyncMock(side_effect=session.rollback)
    return db


def _setup_http_fixture(
    monkeypatch,
    tmp_path,
    *,
    old_csv: bytes,
    new_csv: bytes,
    old_version: str = "v-old-1",
    new_run_bronze_version: str = "v-new-1",
    new_job_output_version: str = "v-new-1",
    actual_new_minio_version: str | None = None,
    cleaning_status: str = "COMPLETED",
    publication_status: str = "PUBLISHED",
    new_quarantine: list[dict[str, Any]] | None = None,
    allowed_columns: list[str] | None = None,
):
    storage = _FakeMinio()
    monkeypatch.setattr(quarantine_records, "bronze_reader_client", lambda **_: storage)

    old_uri = f"s3://{settings.minio_bucket_bronze}/hr/2026-11-11/job-100/employees.csv"
    new_uri = f"s3://{settings.minio_bucket_bronze}/hr/2026-11-11/job-101/employees.csv"
    old_evidence = _bronze_evidence(
        tmp_path=tmp_path,
        storage=storage,
        filename="employees.csv",
        content=old_csv,
        uri=old_uri,
        version_id=old_version,
        allowed_columns=allowed_columns,
    )
    new_evidence = _bronze_evidence(
        tmp_path=tmp_path,
        storage=storage,
        filename="employees.csv",
        content=new_csv,
        uri=new_uri,
        version_id=new_job_output_version,
        actual_version=actual_new_minio_version or new_job_output_version,
    )

    engine = _sqlite_engine()
    now = datetime.now(UTC)
    issue_id = str(uuid4())
    old_run_id = str(uuid4())
    new_run_id = str(uuid4())
    rec_1_id = str(uuid4())
    rec_2_id = str(uuid4())

    with Session(engine) as session, session.begin():
        role_ids = {}
        for idx, role in enumerate(RoleCode, start=1):
            session.add(Role(role_id=idx, role_code=role.value, role_name=role.value))
            role_ids[role.value] = idx
        session.add(
            Department(
                department_id=7,
                department_code="HR",
                department_name="Nhân sự",
                department_type="OFFICE",
                status="ACTIVE",
            )
        )
        session.add(Position(position_id=9, position_code="STAFF", position_name="Nhân viên"))
        for uid, role_code, dept in (
            (1, RoleCode.DATA_ADMIN.value, None),
            (2, RoleCode.ADMIN.value, None),
            (14, RoleCode.HR_STAFF.value, 7),
            (19, RoleCode.VIEWER.value, None),
        ):
            session.add(
                Employee(
                    employee_id=uid,
                    employee_code=f"E{uid}",
                    full_name=f"User {uid}",
                    employee_status="ACTIVE",
                )
            )
            session.add(
                User(
                    app_user_id=uid,
                    username=f"user{uid}",
                    password_hash="hash",
                    employee_id=uid,
                    is_active=True,
                )
            )
            session.add(UserRole(app_user_id=uid, role_id=role_ids[role_code]))
            session.add(
                LoginSession(login_session_id=uid, app_user_id=uid, session_status="ACTIVE")
            )
            if dept is not None:
                session.add(
                    Assignment(
                        employee_id=uid,
                        department_id=dept,
                        position_id=9,
                        start_date=now.date() - timedelta(days=30),
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
        session.add(
            Dataset(
                dataset_id=9,
                dataset_code="HR_EMPLOYEES",
                dataset_name="Nhân viên",
                domain="hr",
                source_system_id=9,
                data_layer_id=1,
                owner_department_id=7,
                status="ACTIVE",
            )
        )
        session.add(
            DataSource(
                data_source_id=9,
                source_system_id=9,
                source_name="UPLOAD_9_CSV",
                source_type="CSV",
                connection_type="UPLOAD",
                configuration={"delimiter": ",", "encoding": "utf-8", "has_header": True},
                is_active=True,
                ingestion_count=2,
            )
        )
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
                created_at=(now - timedelta(hours=2)).replace(tzinfo=None),
                rows_read=2,
                rows_written=2,
                output={
                    "uri": old_uri,
                    "version_id": old_version,
                    "sha256": old_evidence["sha256"],
                    "size_bytes": old_evidence["size_bytes"],
                    "source_type": "CSV",
                    "parser_options": old_evidence["parser_options"],
                },
            )
        )
        session.add(
            IngestionJob(
                ingestion_job_id=101,
                data_source_id=9,
                dataset_id=9,
                target_layer_id=1,
                job_type="UPLOAD_V1",
                source_table="employees.csv",
                status="SUCCESS",
                stage="COMPLETED",
                requested_by="14",
                created_at=(now - timedelta(hours=1)).replace(tzinfo=None),
                rows_read=3,
                rows_written=3,
                output={
                    "uri": new_uri,
                    "version_id": new_job_output_version,
                    "sha256": new_evidence["sha256"],
                    "size_bytes": new_evidence["size_bytes"],
                    "source_type": "CSV",
                    "parser_options": new_evidence["parser_options"],
                },
            )
        )
        session.execute(
            insert(CLEANING_RUNS).values(
                run_id=old_run_id,
                ingestion_job_id=100,
                dataset_id=9,
                domain="hr",
                entity="employee",
                requested_by="1",
                session="hr",
                bronze_version=old_version,
                cleaning_status="COMPLETED",
                publication_status="BLOCKED",
                records_read=2,
                records_passed=0,
                records_quarantined=2,
                records_collapsed=0,
                records_published=0,
                quarantine_source=old_evidence,
                started_at=now - timedelta(hours=2),
                finished_at=now - timedelta(hours=2),
            )
        )
        session.execute(
            insert(CLEANING_RUNS).values(
                run_id=new_run_id,
                ingestion_job_id=101,
                dataset_id=9,
                domain="hr",
                entity="employee",
                requested_by="1",
                session="hr",
                bronze_version=new_run_bronze_version,
                cleaning_status=cleaning_status,
                publication_status=publication_status,
                records_read=3,
                records_passed=3 - len(new_quarantine or []),
                records_quarantined=len(new_quarantine or []),
                records_collapsed=0,
                records_published=3 - len(new_quarantine or [])
                if publication_status in {"PUBLISHED", "PUBLISHED_WITH_QUARANTINE"}
                else 0,
                silver_snapshot_id=new_run_id
                if publication_status in {"PUBLISHED", "PUBLISHED_WITH_QUARANTINE"}
                else None,
                quarantine_source=new_evidence if new_quarantine else None,
                started_at=now - timedelta(minutes=30),
                finished_at=now - timedelta(minutes=29) if cleaning_status == "COMPLETED" else None,
            )
        )
        session.execute(
            insert(QUARANTINE_RECORDS),
            [
                {
                    "record_id": rec_1_id,
                    "cleaning_run_id": old_run_id,
                    "locator": "record:1",
                    "record_position": 1,
                    "issues": [{"code": "INVALID_FORMAT", "field": "email"}],
                    "error_codes": ["INVALID_FORMAT"],
                },
                {
                    "record_id": rec_2_id,
                    "cleaning_run_id": old_run_id,
                    "locator": "record:2",
                    "record_position": 2,
                    "issues": [{"code": "INVALID_FORMAT", "field": "email"}],
                    "error_codes": ["INVALID_FORMAT"],
                },
            ],
        )
        if new_quarantine:
            session.execute(
                insert(QUARANTINE_RECORDS),
                [
                    {
                        "record_id": str(uuid4()),
                        "cleaning_run_id": new_run_id,
                        **entry,
                    }
                    for entry in new_quarantine
                ],
            )
        session.execute(
            insert(DATA_QUALITY_ISSUES).values(
                issue_id=issue_id,
                dataset_id=9,
                department_id=7,
                domain="hr",
                title="Sửa email nhân viên",
                description="Kiểm tra email sai định dạng",
                status="PENDING_VERIFICATION",
                created_by=1,
                assignee_user_id=14,
                resolution_job_id=101,
                verification_run_id=None,
                created_at=now - timedelta(hours=2),
                updated_at=now - timedelta(minutes=30),
            )
        )
        session.execute(
            insert(DATA_QUALITY_ISSUE_RECORDS),
            [
                {"issue_id": issue_id, "record_id": rec_1_id},
                {"issue_id": issue_id, "record_id": rec_2_id},
            ],
        )

    application = create_app()

    def override_db() -> Iterator[MagicMock]:
        with Session(engine) as session, session.begin():
            yield _async_db(session)

    application.dependency_overrides[get_db] = override_db
    return SimpleNamespace(
        engine=engine,
        app=application,
        issue_id=issue_id,
        old_run_id=old_run_id,
        new_run_id=new_run_id,
        rec_1_id=rec_1_id,
        rec_2_id=rec_2_id,
    )


def _auth_header(user_id: int, role: str) -> dict[str, str]:
    token = create_access_token(str(user_id), claims={"roles": [role], "sid": user_id})
    return {"Authorization": f"Bearer {token}"}


def test_resolve_business_key_fields_for_single_and_composite_keys() -> None:
    assert resolve_business_key_fields(domain="hr", entity="employee", dataset_id=9) == (
        "employee_id",
    )
    assert resolve_business_key_fields(
        domain="hr", entity="employee_qualification", dataset_id=9
    ) == ("employee_id", "qualification_id")
    assert resolve_business_key_fields(domain="admissions", entity="applicant", dataset_id=9) == (
        "applicant_id",
    )
    assert resolve_business_key_fields(domain="unknown", entity="employee", dataset_id=9) == ()


def test_comparison_endpoint_authorization_and_reordered_business_key_matching(
    monkeypatch, tmp_path
) -> None:
    old_csv = (
        b"employee_id,employee_code,email,phone\n"
        b"101,NV0101,bad-email,0901234567\n"
        b"102,NV0102,still-bad,0907654321\n"
    )
    # Dòng mới đảo thứ tự và chèn thêm dòng 999 ở đầu: 101 nằm ở dòng 3, 102 nằm ở dòng 2.
    new_csv = (
        b"employee_id,employee_code,email,phone\n"
        b"999,NV0999,new@unilake.edu.vn,0911111111\n"
        b"102,NV0102,still-bad-2,0907654321\n"
        b"101,NV0101,good@unilake.edu.vn,0901234567\n"
    )
    env = _setup_http_fixture(
        monkeypatch,
        tmp_path,
        old_csv=old_csv,
        new_csv=new_csv,
        publication_status="PUBLISHED_WITH_QUARANTINE",
        new_quarantine=[
            {
                "locator": "record:2",
                "record_position": 2,
                "issues": [{"code": "INVALID_FORMAT", "field": "email"}],
                "error_codes": ["INVALID_FORMAT"],
            }
        ],
    )
    try:
        url = f"/api/v1/data-quality-issues/{env.issue_id}/comparison"
        with TestClient(env.app) as client:
            assert client.get(url).status_code == 401
            assert (
                client.get(url, headers=_auth_header(14, RoleCode.HR_STAFF.value)).status_code
                == 403
            )
            assert (
                client.get(url, headers=_auth_header(19, RoleCode.VIEWER.value)).status_code == 403
            )
            assert (
                client.get(
                    f"{url}?cleaning_run_id={uuid4()}",
                    headers=_auth_header(1, RoleCode.DATA_ADMIN.value),
                ).status_code
                == 404
            )

            admin_resp = client.get(url, headers=_auth_header(2, RoleCode.ADMIN.value))
            assert admin_resp.status_code == 200

            resp = client.get(
                f"{url}?cleaning_run_id={env.new_run_id}",
                headers=_auth_header(1, RoleCode.DATA_ADMIN.value),
            )
            assert resp.status_code == 200
            assert resp.headers["Cache-Control"] == "no-store"

            body = resp.json()
            assert body["issue_status"] == "PENDING_VERIFICATION"
            assert body["manual_verification_required"] is True
            assert body["business_key_fields"] == ["employee_id"]
            assert body["cleaning_run"]["cleaning_run_id"] == env.new_run_id
            assert body["summary"] == {
                "total": 2,
                "resolved": 1,
                "still_invalid": 1,
                "unmatched": 0,
                "manual_review_required": 0,
            }

            by_id = {item["record_id"]: item for item in body["records"]}
            r1 = by_id[env.rec_1_id]
            assert r1["status"] == "RESOLVED"
            assert r1["business_key"] == {"employee_id": "101"}
            assert r1["old_record_position"] == 1
            assert r1["new_record_position"] == 3
            assert r1["new_locator"] == "record:3"
            assert r1["changed_fields"] == ["email"]
            assert r1["old_values"]["email"] == "bad-email"
            assert r1["new_values"]["email"] == "good@unilake.edu.vn"
            assert r1["remaining_error_codes"] == []

            r2 = by_id[env.rec_2_id]
            assert r2["status"] == "STILL_INVALID"
            assert r2["reason"] == "NEW_QUARANTINE_ERRORS"
            assert r2["business_key"] == {"employee_id": "102"}
            assert r2["new_record_position"] == 2
            assert r2["changed_fields"] == ["email"]
            assert r2["remaining_error_codes"] == ["INVALID_FORMAT"]
    finally:
        env.engine.dispose()


@pytest.mark.parametrize(
    ("new_run_bronze_version", "new_job_output_version", "actual_minio_version"),
    [
        ("v-new-1", "v-new-2", None),
        ("v-new-1", "v-new-1", "v-minio-tampered"),
    ],
)
def test_comparison_flags_bronze_version_mismatch_for_manual_review(
    monkeypatch,
    tmp_path,
    new_run_bronze_version: str,
    new_job_output_version: str,
    actual_minio_version: str | None,
) -> None:
    old_csv = b"employee_id,employee_code,email\n101,NV0101,bad-email\n102,NV0102,bad-email-2\n"
    new_csv = (
        b"employee_id,employee_code,email\n"
        b"101,NV0101,good@unilake.edu.vn\n"
        b"102,NV0102,good2@unilake.edu.vn\n"
    )
    env = _setup_http_fixture(
        monkeypatch,
        tmp_path,
        old_csv=old_csv,
        new_csv=new_csv,
        new_run_bronze_version=new_run_bronze_version,
        new_job_output_version=new_job_output_version,
        actual_new_minio_version=actual_minio_version,
    )
    try:
        url = f"/api/v1/data-quality-issues/{env.issue_id}/comparison"
        with TestClient(env.app) as client:
            resp = client.get(url, headers=_auth_header(1, RoleCode.DATA_ADMIN.value))
        assert resp.status_code == 200
        body = resp.json()
        assert body["summary"] == {
            "total": 2,
            "resolved": 0,
            "still_invalid": 0,
            "unmatched": 0,
            "manual_review_required": 2,
        }
        assert all(item["status"] == "MANUAL_REVIEW_REQUIRED" for item in body["records"])
        assert all(item["reason"] == "BRONZE_VERSION_MISMATCH" for item in body["records"])
    finally:
        env.engine.dispose()


def test_comparison_handles_duplicate_missing_unmatched_and_unpublished_cleaning(
    monkeypatch, tmp_path
) -> None:
    # Record 1: key=101 -> trùng business key trong file mới.
    # Record 2: key="" -> thiếu khóa ngay ở bản ghi cũ.
    old_csv = b"employee_id,employee_code,email\n101,NV0101,bad-email\n,NV0102,bad-email-2\n"
    new_csv = (
        b"employee_id,employee_code,email\n"
        b"101,NV0101,good1@unilake.edu.vn\n"
        b"101,NV0101,good2@unilake.edu.vn\n"
    )
    env = _setup_http_fixture(
        monkeypatch,
        tmp_path,
        old_csv=old_csv,
        new_csv=new_csv,
        publication_status="BLOCKED",
        new_quarantine=[
            {
                "locator": "record:1",
                "record_position": 1,
                "issues": [{"code": "DUPLICATE_PRIMARY_KEY", "key_fields": ["employee_id"]}],
                "error_codes": ["DUPLICATE_PRIMARY_KEY"],
            },
            {
                "locator": "record:2",
                "record_position": 2,
                "issues": [{"code": "DUPLICATE_PRIMARY_KEY", "key_fields": ["employee_id"]}],
                "error_codes": ["DUPLICATE_PRIMARY_KEY"],
            },
        ],
    )
    try:
        url = f"/api/v1/data-quality-issues/{env.issue_id}/comparison"
        with TestClient(env.app) as client:
            resp = client.get(url, headers=_auth_header(1, RoleCode.DATA_ADMIN.value))
        assert resp.status_code == 200
        body = resp.json()
        by_id = {item["record_id"]: item for item in body["records"]}
        assert by_id[env.rec_1_id]["status"] == "MANUAL_REVIEW_REQUIRED"
        assert by_id[env.rec_1_id]["reason"] == "DUPLICATE_BUSINESS_KEY_IN_NEW_BRONZE"
        assert by_id[env.rec_1_id]["remaining_error_codes"] == ["DUPLICATE_PRIMARY_KEY"]
        assert by_id[env.rec_2_id]["status"] == "MANUAL_REVIEW_REQUIRED"
        assert by_id[env.rec_2_id]["reason"] == "BUSINESS_KEY_MISSING"
    finally:
        env.engine.dispose()


def test_comparison_does_not_mark_resolved_when_cleaning_completed_but_silver_not_published(
    monkeypatch, tmp_path
) -> None:
    # Bản ghi 101 đổi giá trị & không bị quarantine, nhưng publication_status=BLOCKED.
    # Bản ghi 102 bị xóa/đổi khóa khỏi file mới -> UNMATCHED.
    old_csv = b"employee_id,employee_code,email\n101,NV0101,bad-email\n102,NV0102,bad-email-2\n"
    new_csv = (
        b"employee_id,employee_code,email\n"
        b"101,NV0101,good@unilake.edu.vn\n"
        b"999,NV0999,other@unilake.edu.vn\n"
    )
    env = _setup_http_fixture(
        monkeypatch,
        tmp_path,
        old_csv=old_csv,
        new_csv=new_csv,
        cleaning_status="COMPLETED",
        publication_status="BLOCKED",
    )
    try:
        url = f"/api/v1/data-quality-issues/{env.issue_id}/comparison"
        with TestClient(env.app) as client:
            resp = client.get(url, headers=_auth_header(1, RoleCode.DATA_ADMIN.value))
        assert resp.status_code == 200
        body = resp.json()
        by_id = {item["record_id"]: item for item in body["records"]}
        assert by_id[env.rec_1_id]["status"] == "MANUAL_REVIEW_REQUIRED"
        assert by_id[env.rec_1_id]["reason"] == "SILVER_NOT_PUBLISHED"
        assert by_id[env.rec_1_id]["changed_fields"] == ["email"]
        assert by_id[env.rec_2_id]["status"] == "UNMATCHED"
        assert by_id[env.rec_2_id]["reason"] == "KEY_NOT_FOUND_IN_NEW_BRONZE"
    finally:
        env.engine.dispose()


def test_comparison_respects_allowed_columns_and_rejects_unchanged_values_as_manual_review(
    monkeypatch, tmp_path
) -> None:
    old_csv = (
        b"employee_id,employee_code,full_name,email\n"
        b"101,NV0101,Secret Name,bad-email\n"
        b"102,NV0102,Secret Name 2,bad-email-2\n"
    )
    # 101: email không đổi (NO_FIELD_VALUE_CHANGED); 102: đổi email và không lỗi (RESOLVED).
    new_csv = (
        b"employee_id,employee_code,full_name,email\n"
        b"101,NV0101,Secret Name,bad-email\n"
        b"102,NV0102,Secret Name 2,fixed@unilake.edu.vn\n"
    )
    env = _setup_http_fixture(
        monkeypatch,
        tmp_path,
        old_csv=old_csv,
        new_csv=new_csv,
        publication_status="PUBLISHED",
        allowed_columns=["employee_id", "employee_code", "email"],
    )
    try:
        url = f"/api/v1/data-quality-issues/{env.issue_id}/comparison"
        with TestClient(env.app) as client:
            resp = client.get(url, headers=_auth_header(1, RoleCode.DATA_ADMIN.value))
        assert resp.status_code == 200
        body = resp.json()
        by_id = {item["record_id"]: item for item in body["records"]}
        assert "full_name" not in by_id[env.rec_1_id]["old_values"]
        assert "full_name" not in by_id[env.rec_1_id]["new_values"]
        assert by_id[env.rec_1_id]["status"] == "MANUAL_REVIEW_REQUIRED"
        assert by_id[env.rec_1_id]["reason"] == "NO_FIELD_VALUE_CHANGED"
        assert by_id[env.rec_2_id]["status"] == "RESOLVED"
    finally:
        env.engine.dispose()


@pytest.mark.asyncio
async def test_build_issue_comparison_composite_key_and_missing_key_schema(
    monkeypatch, tmp_path
) -> None:
    storage = _FakeMinio()
    monkeypatch.setattr(quarantine_records, "bronze_reader_client", lambda **_: storage)
    old_csv = b"employee_id,qualification_id,institution\n10,5,Old Uni\n"
    new_csv = b"employee_id,qualification_id,institution\n10,5,New Uni\n"
    old_uri = f"s3://{settings.minio_bucket_bronze}/hr/2026-11-11/job-10/eq.csv"
    new_uri = f"s3://{settings.minio_bucket_bronze}/hr/2026-11-11/job-11/eq.csv"
    old_ev = _bronze_evidence(
        tmp_path=tmp_path,
        storage=storage,
        filename="eq.csv",
        content=old_csv,
        uri=old_uri,
        version_id="v1",
    )
    new_ev = _bronze_evidence(
        tmp_path=tmp_path,
        storage=storage,
        filename="eq.csv",
        content=new_csv,
        uri=new_uri,
        version_id="v2",
    )
    now = datetime.now(UTC)
    result = await build_issue_comparison(
        issue={
            "issue_id": str(uuid4()),
            "dataset_id": 9,
            "domain": "hr",
            "status": "PENDING_VERIFICATION",
            "resolution_job_id": 11,
        },
        old_records=[
            {
                "record_id": str(uuid4()),
                "cleaning_run_id": str(uuid4()),
                "locator": "record:1",
                "record_position": 1,
                "bronze_version": "v1",
                "error_codes": ["INVALID_FORMAT"],
                "issues": [{"code": "INVALID_FORMAT", "field": "institution"}],
                "quarantine_source": old_ev,
                "data_source_id": 9,
                "source_table": "eq.csv",
            }
        ],
        new_run={
            "run_id": str(uuid4()),
            "ingestion_job_id": 11,
            "dataset_id": 9,
            "domain": "hr",
            "entity": "employee_qualification",
            "bronze_version": "v2",
            "cleaning_status": "COMPLETED",
            "publication_status": "PUBLISHED",
            "records_read": 1,
            "records_passed": 1,
            "records_quarantined": 0,
            "records_collapsed": 0,
            "records_published": 1,
            "silver_snapshot_id": str(uuid4()),
            "started_at": now,
            "finished_at": now,
            "data_source_id": 9,
            "source_table": "eq.csv",
            "quarantine_source": None,
            "job_output": {
                "uri": new_uri,
                "version_id": "v2",
                "sha256": new_ev["sha256"],
                "size_bytes": new_ev["size_bytes"],
                "source_type": "CSV",
                "parser_options": new_ev["parser_options"],
            },
            "job_upload_input": None,
        },
        new_quarantine_records=[],
        source_metadata_by_id={
            9: {"source_system_id": 9, "source_type": "CSV", "configuration": {}}
        },
    )
    assert result["business_key_fields"] == ["employee_id", "qualification_id"]
    assert result["records"][0]["business_key"] == {"employee_id": "10", "qualification_id": "5"}
    assert result["records"][0]["status"] == "RESOLVED"
    assert result["records"][0]["changed_fields"] == ["institution"]


def test_comparison_requires_completed_cleaning_and_valid_run_and_keeps_verify_manual(
    monkeypatch, tmp_path
) -> None:
    old_csv = b"employee_id,employee_code,email\n101,NV0101,bad-email\n102,NV0102,bad-email-2\n"
    new_csv = (
        b"employee_id,employee_code,email\n"
        b"101,NV0101,good@unilake.edu.vn\n"
        b"102,NV0102,good2@unilake.edu.vn\n"
    )
    env = _setup_http_fixture(
        monkeypatch,
        tmp_path,
        old_csv=old_csv,
        new_csv=new_csv,
        cleaning_status="RUNNING",
        publication_status="NOT_STARTED",
    )
    try:
        other_run_id = str(uuid4())
        now = datetime.now(UTC)
        with Session(env.engine) as session, session.begin():
            session.execute(
                insert(CLEANING_RUNS).values(
                    run_id=other_run_id,
                    ingestion_job_id=101,
                    dataset_id=9,
                    domain="hr",
                    entity="department",
                    requested_by="1",
                    session="hr",
                    bronze_version="v-new-1",
                    cleaning_status="COMPLETED",
                    publication_status="PUBLISHED",
                    records_read=1,
                    records_passed=1,
                    records_quarantined=0,
                    records_collapsed=0,
                    records_published=1,
                    silver_snapshot_id=other_run_id,
                    started_at=now,
                    finished_at=now,
                )
            )

        url = f"/api/v1/data-quality-issues/{env.issue_id}/comparison"
        headers = _auth_header(1, RoleCode.DATA_ADMIN.value)
        with TestClient(env.app) as client:
            # 1. cleaning_run_id sai entity -> 422 INVALID_VERIFICATION_RUN.
            wrong_run = client.get(f"{url}?cleaning_run_id={other_run_id}", headers=headers)
            assert wrong_run.status_code == 422
            assert wrong_run.json()["error"]["code"] == "INVALID_VERIFICATION_RUN"

            # 2. Cleaning đang RUNNING -> MANUAL_REVIEW_REQUIRED (CLEANING_NOT_COMPLETED).
            running_resp = client.get(url, headers=headers)
            assert running_resp.status_code == 200
            assert all(
                item["status"] == "MANUAL_REVIEW_REQUIRED"
                and item["reason"] == "CLEANING_NOT_COMPLETED"
                for item in running_resp.json()["records"]
            )
            assert running_resp.json()["issue_status"] == "PENDING_VERIFICATION"

            # 3. Khi hoàn tất & PUBLISHED -> comparison trả RESOLVED nhưng issue vẫn PENDING_VERIFICATION
            # cho tới khi Admin xác minh thủ công qua POST /verification.
            with Session(env.engine) as session, session.begin():
                session.execute(
                    text(
                        "UPDATE cleaning_run SET cleaning_status = 'COMPLETED', "
                        "publication_status = 'PUBLISHED', finished_at = :now "
                        "WHERE run_id = :run_id"
                    ),
                    {"now": now, "run_id": env.new_run_id},
                )

            ready_resp = client.get(url, headers=headers)
            assert ready_resp.status_code == 200
            assert ready_resp.json()["summary"]["resolved"] == 2
            assert ready_resp.json()["issue_status"] == "PENDING_VERIFICATION"

            # Xác minh thủ công thiếu record vẫn bị verify_issue() chặn như cũ.
            incomplete_verify = client.post(
                f"/api/v1/data-quality-issues/{env.issue_id}/verification",
                headers=headers,
                json={
                    "cleaning_run_id": env.new_run_id,
                    "verified": True,
                    "note": "Thiếu record",
                    "confirmed_record_ids": [env.rec_1_id],
                },
            )
            assert incomplete_verify.status_code == 422
            assert incomplete_verify.json()["error"]["code"] == "VERIFICATION_RECORDS_INCOMPLETE"

            verify_ok = client.post(
                f"/api/v1/data-quality-issues/{env.issue_id}/verification",
                headers=headers,
                json={
                    "cleaning_run_id": env.new_run_id,
                    "verified": True,
                    "note": "Đã đối chiếu xong",
                    "confirmed_record_ids": [env.rec_1_id, env.rec_2_id],
                },
            )
            assert verify_ok.status_code == 200
            assert verify_ok.json()["status"] == "VERIFIED"
    finally:
        env.engine.dispose()


@pytest.mark.asyncio
async def test_build_issue_comparison_unknown_entity_requires_manual_review(
    monkeypatch, tmp_path
) -> None:
    storage = _FakeMinio()
    monkeypatch.setattr(quarantine_records, "bronze_reader_client", lambda **_: storage)
    csv_bytes = b"col_a,col_b\n1,old\n"
    uri = f"s3://{settings.minio_bucket_bronze}/hr/2026-11-11/job-10/custom.csv"
    ev = _bronze_evidence(
        tmp_path=tmp_path,
        storage=storage,
        filename="custom.csv",
        content=csv_bytes,
        uri=uri,
        version_id="v1",
    )
    now = datetime.now(UTC)
    result = await build_issue_comparison(
        issue={
            "issue_id": str(uuid4()),
            "dataset_id": 9,
            "domain": "hr",
            "status": "PENDING_VERIFICATION",
            "resolution_job_id": 11,
        },
        old_records=[
            {
                "record_id": str(uuid4()),
                "cleaning_run_id": str(uuid4()),
                "locator": "record:1",
                "record_position": 1,
                "bronze_version": "v1",
                "error_codes": ["INVALID_FORMAT"],
                "issues": [{"code": "INVALID_FORMAT", "field": "col_b"}],
                "quarantine_source": ev,
                "data_source_id": 9,
                "source_table": "custom.csv",
            }
        ],
        new_run={
            "run_id": str(uuid4()),
            "ingestion_job_id": 11,
            "dataset_id": 9,
            "domain": "hr",
            "entity": "unknown_entity",
            "bronze_version": "v1",
            "cleaning_status": "COMPLETED",
            "publication_status": "PUBLISHED",
            "records_read": 1,
            "records_passed": 1,
            "records_quarantined": 0,
            "records_collapsed": 0,
            "records_published": 1,
            "silver_snapshot_id": str(uuid4()),
            "started_at": now,
            "finished_at": now,
            "data_source_id": 9,
            "source_table": "custom.csv",
            "quarantine_source": None,
            "job_output": ev,
            "job_upload_input": None,
        },
        new_quarantine_records=[],
        source_metadata_by_id={
            9: {"source_system_id": 9, "source_type": "CSV", "configuration": {}}
        },
    )
    assert result["business_key_fields"] == []
    assert result["records"][0]["status"] == "MANUAL_REVIEW_REQUIRED"
    assert result["records"][0]["reason"] == "BUSINESS_KEY_UNAVAILABLE"
