"""Kiểm tra tải file qua HTTP, hàng đợi và worker bằng file thật, kho lưu trữ cô lập."""

from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.bronze_capture import store_bronze
from app.core.config import settings
from app.core.deps import get_current_user
from app.core.exceptions import AppException, app_exception_handler
from app.dependencies import get_db
from app.modules.datasources.models import DataLayer, Dataset, DataSource, SourceSystem
from app.modules.ingestion import upload_capture
from app.modules.ingestion.execution import IngestionExecutionService, validate_context
from app.modules.ingestion.models import IngestionJob
from app.modules.ingestion.router import router
from app.modules.ingestion.upload_capture import UploadCapture, upload_request
from app.modules.staff.services import uploads


class MemoryStore:
    def __init__(self):
        self.objects = {}
        self.reads = []
        self.writes = []
        self.bad_version = False

    def get_bucket_versioning(self, _bucket):
        return SimpleNamespace(status="Enabled")

    def fput_object(self, bucket, key, path, metadata=None):
        version = f"v{len(self.objects) + 1}"
        self.objects[bucket, key, version] = (Path(path).read_bytes(), metadata or {})
        self.writes.append((bucket, key, version))
        return SimpleNamespace(version_id=version)

    def stat_object(self, bucket, key, version_id):
        content, metadata = self.objects[bucket, key, version_id]
        return SimpleNamespace(
            version_id=version_id,
            size=len(content),
            metadata={f"x-amz-meta-{name}": value for name, value in metadata.items()},
        )

    def get_object(self, bucket, key, version_id):
        self.reads.append((bucket, key, version_id))
        content, _ = self.objects[bucket, key, version_id]
        response = Mock()
        response.headers = {"x-amz-version-id": "wrong" if self.bad_version else version_id}
        response.stream.return_value = [content[i : i + 17] for i in range(0, len(content), 17)]
        return response


def source_context(source_type="CSV"):
    return (
        DataSource(
            data_source_id=11,
            source_system_id=3,
            source_name="UPLOAD_7_CSV",
            source_type=source_type,
            connection_type="UPLOAD",
            configuration={"upload_dataset_id": 7},
            is_active=True,
        ),
        Dataset(dataset_id=7, source_system_id=3, data_layer_id=1, domain="hr", status="ACTIVE"),
        DataLayer(data_layer_id=1, layer_code="BRONZE"),
        SourceSystem(source_system_id=3, is_active=True),
    )


@pytest.fixture
def harness(monkeypatch):
    storage = MemoryStore()
    context = source_context()
    calls = []
    db = AsyncMock()

    async def target(_db, dataset_id, source_type, _actor, *, allowed_domains=None):
        assert dataset_id == 7
        assert allowed_domains in {None, frozenset({"hr"})}
        context[0].source_type = source_type
        return context[:2]

    async def create(**values):
        # Mô phỏng lưu job; file bắt buộc đã tồn tại trong kho tiếp nhận.
        assert storage.writes
        calls.append(values)
        return IngestionJob(ingestion_job_id=42, created_at=datetime(2026, 11, 9), **values)

    repository = Mock()
    repository.create = AsyncMock(side_effect=create)
    monkeypatch.setattr(uploads, "upload_target", target)
    monkeypatch.setattr(uploads, "IngestionRepository", lambda _: repository)
    monkeypatch.setattr(uploads, "upload_writer_client", lambda: storage)
    monkeypatch.setattr(upload_capture, "upload_reader_client", lambda: storage)
    monkeypatch.setattr("app.bronze_capture.bronze_writer_client", lambda: storage)
    monkeypatch.setattr("app.bronze_capture.bronze_verifier_client", lambda: storage)
    app = FastAPI()
    app.add_exception_handler(AppException, app_exception_handler)
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: {"sub": "1", "roles": ["DATA_ADMIN"]}
    app.dependency_overrides[get_db] = lambda: db
    return SimpleNamespace(
        storage=storage, context=context, calls=calls, app=app, db=db, repository=repository
    )


def excel_bytes(*, formula=False):
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "NhanSu"
    sheet.append(["id", "name"])
    sheet.append(["001", "=1+1" if formula else "Nhân viên"])
    stream = io.BytesIO()
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("people.csv", b'id,name\r\n001,"first\nrecord"\r\n'),
        ("people.xlsx", excel_bytes()),
        ("people.xlsm", excel_bytes()),
    ],
)
async def test_http_upload_through_worker_preserves_original_bronze_bytes(
    harness, filename, content
):
    response = TestClient(harness.app).post(
        "/ingestion/uploads", files={"file": (filename, content)}, data={"dataset_id": "7"}
    )
    assert response.status_code == 202, response.text
    assert response.json()["job_type"] == "UPLOAD_V1"
    assert response.json()["status"] == "QUEUED"
    assert all(bucket == settings.minio_bucket_uploads for bucket, _, _ in harness.storage.writes)
    job = IngestionJob(ingestion_job_id=42, created_at=datetime(2026, 11, 9), **harness.calls[0])
    repository = AsyncMock()
    repository.context.return_value = harness.context
    await IngestionExecutionService(repository).execute(
        job, lambda: UploadCapture(job), store_bronze
    )
    output = repository.finish.call_args.kwargs.get("output")
    assert output is not None, repository.finish.call_args
    assert job.upload_input is not None
    assert output["sha256"] == hashlib.sha256(content).hexdigest()
    assert output["source_version_id"] == job.upload_input["version_id"]
    assert repository.save_validation.call_args.args[1].can_accept
    if filename.endswith("csv"):
        assert output["parser_options"]["has_header"] is True
    else:
        assert output["parser_options"]["sheet_name"] == "NhanSu"
    bronze = [
        (key, value)
        for key, value in harness.storage.objects.items()
        if key[0] == settings.minio_bucket_bronze
    ]
    assert len(bronze) == 1
    assert bronze[0][1][0] == content
    assert (
        output["lineage"]["derivations"][0]["inputs"][0]["checksum"] == job.upload_input["sha256"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("filename", ["people.csv", "people.xlsx"])
async def test_retry_reuses_bronze_but_keeps_rows_parser_and_validation(harness, filename):
    content = b"id,name\n1,Nguyen An\n" if filename.endswith("csv") else excel_bytes()
    response = TestClient(harness.app).post(
        "/ingestion/uploads", files={"file": (filename, content)}, data={"dataset_id": "7"}
    )
    assert response.status_code == 202
    original = IngestionJob(
        ingestion_job_id=42, created_at=datetime(2026, 11, 9), **harness.calls[0]
    )
    repository = AsyncMock()
    repository.context.return_value = harness.context
    await IngestionExecutionService(repository).execute(
        original, lambda: UploadCapture(original), store_bronze
    )
    checkpoint = repository.checkpoint_bronze.call_args.args[1]
    writes_before_retry = len(harness.storage.writes)
    retried = IngestionJob(
        ingestion_job_id=43,
        created_at=datetime(2026, 11, 9),
        bronze_output=checkpoint,
        **harness.calls[0],
    )
    # Bao phủ cả bằng chứng cũ chỉ lưu thông tin file, chưa có cách đọc hay số dòng.
    assert retried.bronze_output is not None
    for key in ("records_read", "parser_options", "source_type", "lineage"):
        retried.bronze_output.pop(key, None)
    repository = AsyncMock()
    repository.context.return_value = harness.context
    store = Mock(side_effect=AssertionError("Không được ghi thêm Bronze khi dùng lại file"))
    await IngestionExecutionService(repository).execute(
        retried, lambda: UploadCapture(retried), store
    )
    output = repository.finish.call_args.kwargs["output"]
    assert output["reused_bronze"] is True
    assert output["records_read"] == 1
    assert output["source_type"] == harness.context[0].source_type
    assert output["parser_options"]
    assert output["uri"] == checkpoint["uri"]
    assert repository.save_validation.call_args.args[1].ingestion_job_id == 43
    repository.stage.assert_any_await(43, "RECONCILING", 1)
    store.assert_not_called()
    assert len(harness.storage.writes) == writes_before_retry


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "code"),
    [
        (b"id,id\n1,2\n", "HEADER_DUPLICATE"),
        (b"id,name\n1\n", "SOURCE_SCHEMA_MISMATCH"),
        (b"id,name\n", "EMPTY_SOURCE"),
        (b"id,name\n1,\xff\n", "ENCODING_ERROR"),
    ],
)
async def test_invalid_csv_is_queued_but_never_published_to_bronze(harness, content, code):
    response = TestClient(harness.app).post(
        "/ingestion/uploads", files={"file": ("bad.csv", content)}, data={"dataset_id": "7"}
    )
    assert response.status_code == 202
    job = IngestionJob(ingestion_job_id=42, created_at=datetime(2026, 11, 9), **harness.calls[0])
    repository = AsyncMock()
    repository.context.return_value = harness.context
    await IngestionExecutionService(repository).execute(
        job, lambda: UploadCapture(job), store_bronze
    )
    repository.finish.assert_awaited_once_with(42, error="BRONZE_INPUT_VALIDATION_BLOCKED")
    report = repository.save_validation.call_args.args[1]
    assert not report.can_accept
    assert code in {issue.code for issue in report.issues}
    assert not any(
        bucket == settings.minio_bucket_bronze for bucket, _, _ in harness.storage.writes
    )


@pytest.mark.parametrize(
    ("filename", "content", "status"),
    [("a.csv", b"", 422), ("a.txt", b"hello", 415), ("../a.csv", b"x", 422), ("a.xls", b"x", 415)],
)
def test_invalid_uploads_do_not_create_jobs(harness, filename, content, status):
    response = TestClient(harness.app).post(
        "/ingestion/uploads", files={"file": (filename, content)}, data={"dataset_id": "7"}
    )
    assert response.status_code == status
    harness.repository.create.assert_not_called()
    assert harness.storage.writes == []


def test_limits_authorization_and_invalid_parser_are_checked(harness, monkeypatch):
    client = TestClient(harness.app)
    monkeypatch.setattr(settings, "upload_max_bytes", 3)
    response = client.post(
        "/ingestion/uploads", files={"file": ("a.csv", b"1234")}, data={"dataset_id": "7"}
    )
    assert response.status_code == 413
    response = client.post(
        "/ingestion/uploads",
        files={"file": ("a.csv", b"1")},
        data={"dataset_id": "7", "parser_options": json.dumps({"location": "/private"})},
    )
    assert response.status_code == 422
    harness.app.dependency_overrides[get_current_user] = lambda: {"sub": "2", "roles": ["HR_STAFF"]}
    response = client.post(
        "/ingestion/uploads", files={"file": ("a.csv", b"1")}, data={"dataset_id": "7"}
    )
    assert response.status_code == 202


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["version", "checksum", "formula", "expanded", "sheet"])
async def test_pinned_version_and_excel_validation_block_bad_inputs(harness, monkeypatch, failure):
    filename = "a.csv" if failure in {"version", "checksum"} else "a.xlsx"
    content = (
        b"id,name\n1,test\n"
        if filename.endswith("csv")
        else excel_bytes(formula=failure == "formula")
    )
    data = {"dataset_id": "7"}
    if failure == "sheet":
        data["parser_options"] = json.dumps({"sheet_name": "missing"})
    response = TestClient(harness.app).post(
        "/ingestion/uploads", files={"file": (filename, content)}, data=data
    )
    assert response.status_code == 202
    job = IngestionJob(ingestion_job_id=42, created_at=datetime(2026, 11, 9), **harness.calls[0])
    if failure == "version":
        harness.storage.bad_version = True
    elif failure == "checksum":
        assert job.upload_input is not None
        job.upload_input["sha256"] = "a" * 64
    elif failure == "expanded":
        monkeypatch.setattr(settings, "upload_max_expanded_bytes", 1)
    repository = AsyncMock()
    repository.context.return_value = harness.context
    await IngestionExecutionService(repository).execute(
        job, lambda: UploadCapture(job), store_bronze
    )
    repository.finish.assert_awaited_once_with(42, error="BRONZE_INPUT_VALIDATION_BLOCKED")
    assert not any(
        bucket == settings.minio_bucket_bronze for bucket, _, _ in harness.storage.writes
    )


def test_normal_ingestion_cannot_read_an_upload_source():
    with pytest.raises(ValueError, match="USE_INGESTION_UPLOAD_ENDPOINT"):
        validate_context(source_context(), "a.csv")


def test_upload_request_rejects_other_datasets_and_unpinned_locations():
    context = source_context()
    job = IngestionJob(
        ingestion_job_id=42,
        data_source_id=11,
        dataset_id=7,
        target_layer_id=1,
        source_table="a.csv",
        upload_input={},
    )
    with pytest.raises(ValueError, match="UPLOAD_BINDING_MISMATCH"):
        upload_request(job, context)


@pytest.mark.asyncio
async def test_upload_target_creates_and_reuses_source_with_real_sql():
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    engine = create_engine("sqlite://", execution_options={"schema_translate_map": {"app": None}})
    # SQLite chỉ phục vụ kiểm thử: tạo đủ cột để chạy truy vấn thật mà không gọi dịch vụ bên ngoài.
    with engine.begin() as conn:
        for model in (SourceSystem, DataLayer, Dataset, DataSource):
            definitions = []
            for column in model.__table__.columns:
                if column.primary_key:
                    definitions.append(f'"{column.name}" INTEGER PRIMARY KEY')
                elif column.name in {"created_at", "updated_at"}:
                    definitions.append(f'"{column.name}" TIMESTAMP DEFAULT CURRENT_TIMESTAMP')
                elif column.name == "configuration":
                    definitions.append(f"\"{column.name}\" JSON DEFAULT '{{}}'")
                else:
                    definitions.append(
                        f'"{column.name}" {"INTEGER" if column.type.python_type in {int, bool} else "TEXT"}'
                    )
            conn.execute(text(f'CREATE TABLE "{model.__tablename__}" ({", ".join(definitions)})'))
    with Session(engine, expire_on_commit=False) as session:
        session.add_all(
            [
                SourceSystem(
                    source_system_id=3,
                    source_code="HR",
                    source_name="Nhân sự",
                    source_type="FILE",
                    is_active=True,
                ),
                DataLayer(data_layer_id=1, layer_code="BRONZE", layer_name="Bronze"),
                Dataset(
                    dataset_id=7,
                    dataset_code="HR",
                    dataset_name="Nhân sự",
                    domain="hr",
                    source_system_id=3,
                    data_layer_id=1,
                    status="ACTIVE",
                ),
            ]
        )
        session.commit()
        db = Mock()
        db.scalar = AsyncMock(side_effect=session.scalar)
        db.get = AsyncMock(side_effect=session.get)
        db.flush = AsyncMock(side_effect=session.flush)
        db.commit = AsyncMock(side_effect=session.commit)
        db.add = session.add
        first, _ = await uploads.upload_target(db, 7, "CSV", "1")
        second, _ = await uploads.upload_target(db, 7, "CSV", "1")
        assert first.data_source_id == second.data_source_id
        assert first.connection_type == "UPLOAD"
        assert first.configuration == {"upload_dataset_id": 7}
        assert first.location is None
        assert len(session.query(DataSource).all()) == 1
        with pytest.raises(AppException) as exc:
            await uploads.upload_target(db, 999, "CSV", "1")
        assert exc.value.status_code == 404
        # Nhân viên HR chỉ được chọn bộ dữ liệu đã đăng ký trong miền HR.
        with pytest.raises(AppException) as exc:
            await uploads.upload_target(db, 7, "CSV", "1", allowed_domains=frozenset({"academic"}))
        assert exc.value.code == "UPLOAD_DOMAIN_FORBIDDEN"
        first.is_active = False
        session.commit()
        with pytest.raises(AppException) as exc:
            await uploads.upload_target(db, 7, "CSV", "1")
        assert exc.value.status_code == 422
    engine.dispose()


def test_storage_failure_never_returns_accepted_job(harness, monkeypatch):
    def fail(*_args, **_kwargs):
        raise OSError("thông tin nội bộ không được trả ra")

    monkeypatch.setattr(uploads, "stage_file", fail)
    response = TestClient(harness.app).post(
        "/ingestion/uploads", files={"file": ("a.csv", b"id\n1\n")}, data={"dataset_id": "7"}
    )
    assert response.status_code == 503
    assert "thông tin nội bộ" not in response.text
    harness.repository.create.assert_not_called()


def test_database_failure_does_not_report_success(harness):
    from sqlalchemy.exc import SQLAlchemyError

    harness.repository.create.side_effect = SQLAlchemyError("thông tin nội bộ")
    response = TestClient(harness.app).post(
        "/ingestion/uploads", files={"file": ("a.csv", b"id\n1\n")}, data={"dataset_id": "7"}
    )
    assert response.status_code == 503
    assert len(harness.storage.writes) == 1
    assert all(bucket == settings.minio_bucket_uploads for bucket, _, _ in harness.storage.writes)


def test_missing_file_or_dataset_is_rejected(harness):
    client = TestClient(harness.app)
    assert client.post("/ingestion/uploads", data={"dataset_id": "7"}).status_code == 422
    assert (
        client.post("/ingestion/uploads", files={"file": ("a.csv", b"id\n1\n")}).status_code == 422
    )
    harness.repository.create.assert_not_called()
