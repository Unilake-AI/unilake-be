"""Thử tải bộ dữ liệu mới qua API, lưu dữ liệu thật trong cơ sở dữ liệu kiểm thử riêng."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.core.exceptions import AppException, app_exception_handler
from app.dependencies import get_db
from app.modules.datasources.datasets_router import router as datasets_router
from app.modules.datasources.models import DataLayer, Dataset, DataSource, SourceSystem
from app.modules.ingestion.models import IngestionJob
from app.modules.ingestion.router import router
from app.modules.ingestion.services import uploads
from app.modules.ingestion.services.upload_capture import upload_request
from app.repositories.ingestion_repo import IngestionRepository
from tests.test_ingestion_uploads import MemoryStore


@pytest.fixture
def environment(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'uploads.db'}",
        execution_options={"schema_translate_map": {"app": None}},
    )
    # Chạy câu lệnh lưu và truy vấn thật; chỉ đổi cách khai báo bảng cho cơ sở dữ liệu kiểm thử.
    with engine.begin() as connection:
        for model in (DataLayer, SourceSystem, Dataset, DataSource, IngestionJob):
            definitions = []
            for column in model.__table__.columns:
                if column.primary_key:
                    definition = "INTEGER PRIMARY KEY"
                elif column.name in {"created_at", "updated_at"}:
                    definition = "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
                elif column.type.python_type is dict:
                    definition = "JSON"
                elif column.type.python_type in {int, bool}:
                    definition = "INTEGER"
                else:
                    definition = "TEXT"
                if column.unique:
                    definition += " UNIQUE"
                definitions.append(f'"{column.name}" {definition}')
            connection.execute(
                text(f'CREATE TABLE "{model.__tablename__}" ({", ".join(definitions)})')
            )
    with Session(engine) as session:
        session.add(DataLayer(layer_code="BRONZE", layer_name="Bronze"))
        session.commit()

    async def database():
        with Session(engine, expire_on_commit=False) as session:
            db = SimpleNamespace(add=session.add)
            for method in ("scalar", "scalars", "get", "flush", "execute", "commit", "rollback"):
                setattr(db, method, AsyncMock(side_effect=getattr(session, method)))
            try:
                yield db
                session.commit()
            except Exception:
                session.rollback()
                raise

    store = MemoryStore()
    monkeypatch.setattr(uploads, "upload_writer_client", lambda: store)
    user = {"sub": "19", "roles": ["HR_STAFF"]}
    app = FastAPI()
    app.add_exception_handler(AppException, app_exception_handler)
    app.include_router(router)
    app.include_router(datasets_router)
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: user
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, engine=engine, user=user, store=store)
    engine.dispose()


def upload(environment, **data):
    return environment.client.post(
        "/ingestion/uploads",
        files={"file": ("employees.csv", b"employee_id,name\n1,Nguyen An\n")},
        data=data,
    )


@pytest.mark.parametrize(
    ("role", "domain"),
    [("HR_STAFF", "hr"), ("ACADEMIC_STAFF", "academic"), ("ADMISSIONS_STAFF", "admissions")],
)
def test_staff_first_upload_creates_dataset_source_and_job_without_technical_ids(
    environment, role, domain
):
    environment.user["roles"] = [role]
    response = upload(environment, dataset_name="  Danh sách nhân viên  ", description="Bản đầu")
    assert response.status_code == 202, response.text
    result = response.json()
    with Session(environment.engine) as session:
        dataset = session.get(Dataset, result["dataset_id"])
        source = session.get(DataSource, result["data_source_id"])
        job = session.get(IngestionJob, result["ingestion_job_id"])
        assert dataset is not None
        assert source is not None
        assert job is not None
        system = session.get(SourceSystem, dataset.source_system_id)
        assert system is not None
        assert dataset.dataset_name == "Danh sách nhân viên"
        assert dataset.dataset_code.startswith("UPLOAD_")
        assert dataset.description == "Bản đầu"
        assert dataset.domain == domain
        assert system.source_type == "UPLOAD"
        assert system.source_code == f"UPLOAD_{domain.upper()}"
        assert source.source_system_id == dataset.source_system_id
        assert source.configuration == {"upload_dataset_id": dataset.dataset_id}
        assert job.job_type == "UPLOAD_V1"
        assert job.status == "QUEUED"
        assert job.root_job_id == job.ingestion_job_id
        evidence = job.upload_input
        assert evidence is not None
        assert evidence["domain"] == domain
        assert evidence["dataset_id"] == dataset.dataset_id
        assert evidence["version_id"]
        layer = session.get(DataLayer, dataset.data_layer_id)
        request = upload_request(job, (source, dataset, layer, system))
        assert request.source_type == "CSV"
        assert request.object_version_id == evidence["version_id"]
    choices = environment.client.get("/datasets").json()
    assert [item["dataset_id"] for item in choices] == [result["dataset_id"]]


def test_next_upload_selects_existing_dataset_and_reuses_its_source(environment):
    first = upload(environment, dataset_name="Nhân viên").json()
    second = upload(environment, dataset_id=str(first["dataset_id"]))
    assert second.status_code == 202, second.text
    assert second.json()["dataset_id"] == first["dataset_id"]
    assert second.json()["data_source_id"] == first["data_source_id"]
    assert second.json()["ingestion_job_id"] != first["ingestion_job_id"]
    with Session(environment.engine) as session:
        assert len(session.scalars(select(Dataset)).all()) == 1
        assert len(session.scalars(select(DataSource)).all()) == 1
        assert len(session.scalars(select(IngestionJob)).all()) == 2


def test_new_datasets_share_managed_source_system_and_reject_duplicate_names(environment):
    assert upload(environment, dataset_name="Nhân viên").status_code == 202
    assert upload(environment, dataset_name="Chức vụ").status_code == 202
    duplicate = upload(environment, dataset_name="  nhân   viên  ")
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["error"]["code"] == "UPLOAD_DATASET_NAME_CONFLICT"
    with Session(environment.engine) as session:
        assert len(session.scalars(select(SourceSystem)).all()) == 1
        assert len(session.scalars(select(Dataset)).all()) == 2
        assert len(session.scalars(select(IngestionJob)).all()) == 2
    assert len(environment.store.writes) == 2


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"dataset_name": "   "},
        {"dataset_name": "a" * 201},
        {"dataset_name": "Nhân viên", "description": "a" * 4001},
        {"dataset_id": "1", "dataset_name": "Nhân viên"},
        {"dataset_id": "1", "domain": "hr"},
        {"dataset_id": "1", "description": "Không sửa mô tả khi tải file"},
        {"dataset_name": "Nhân viên", "domain": "unknown"},
    ],
)
def test_invalid_destination_never_creates_metadata_or_stages_file(environment, data):
    response = upload(environment, **data)
    assert response.status_code == 422, response.text
    assert not environment.store.writes
    with Session(environment.engine) as session:
        assert not session.scalars(select(Dataset)).all()
        assert not session.scalars(select(SourceSystem)).all()
        assert not session.scalars(select(IngestionJob)).all()


def test_staff_cannot_create_in_another_domain_or_upload_to_its_dataset(environment):
    forbidden = upload(environment, dataset_name="Nhân viên", domain="academic")
    assert forbidden.status_code == 403
    environment.user["roles"] = ["ACADEMIC_STAFF"]
    other = upload(environment, dataset_name="Sinh viên").json()
    environment.user["roles"] = ["HR_STAFF"]
    response = upload(environment, dataset_id=str(other["dataset_id"]))
    assert response.status_code == 403
    assert environment.client.get("/datasets").json() == []
    assert len(environment.store.writes) == 1


@pytest.mark.parametrize("role", ["VIEWER", "HR_MANAGER"])
def test_roles_without_upload_rights_cannot_create_dataset(environment, role):
    environment.user["roles"] = [role]
    response = upload(environment, dataset_name="Nhân viên", domain="hr")
    assert response.status_code == 403
    assert not environment.store.writes


@pytest.mark.parametrize("roles", [["ADMIN"], ["DATA_ADMIN"], ["HR_STAFF", "ACADEMIC_STAFF"]])
def test_accounts_with_multiple_choices_must_choose_an_allowed_domain(environment, roles):
    environment.user["roles"] = roles
    missing = upload(environment, dataset_name="Nhân viên")
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "UPLOAD_DOMAIN_REQUIRED"
    response = upload(environment, dataset_name="Nhân viên", domain="hr")
    assert response.status_code == 202, response.text
    if "HR_STAFF" in roles:
        forbidden = upload(environment, dataset_name="Hồ sơ", domain="admissions")
        assert forbidden.status_code == 403


@pytest.mark.parametrize("failure", ["storage", "job", "commit"])
def test_failure_rolls_back_new_dataset_system_source_and_job(environment, monkeypatch, failure):
    if failure == "storage":

        def fail(*_args, **_kwargs):
            raise OSError("Không được lộ thông tin kết nối")

        monkeypatch.setattr(uploads, "stage_file", fail)
    elif failure == "job":
        monkeypatch.setattr(
            IngestionRepository,
            "create",
            AsyncMock(side_effect=SQLAlchemyError("Không được lộ thông tin kết nối")),
        )
    else:

        def fail_commit(_session):
            raise SQLAlchemyError("Không được lộ thông tin kết nối")

        monkeypatch.setattr(Session, "commit", fail_commit)
    response = upload(environment, dataset_name="Nhân viên")
    assert response.status_code == 503
    assert "Không được lộ" not in response.text
    with Session(environment.engine) as session:
        for model in (SourceSystem, Dataset, DataSource, IngestionJob):
            assert not session.scalars(select(model)).all()


@pytest.mark.parametrize("state", ["missing-bronze", "disabled-system", "reserved-code"])
def test_unavailable_catalog_is_not_silently_recreated_or_reenabled(environment, state):
    with Session(environment.engine) as session:
        if state == "missing-bronze":
            session.delete(session.scalar(select(DataLayer)))
        else:
            session.add(
                SourceSystem(
                    source_code="UPLOAD_HR",
                    source_name="Nguồn đã quản lý",
                    source_type="FILE" if state == "reserved-code" else "UPLOAD",
                    is_active=state == "reserved-code",
                )
            )
        session.commit()
    response = upload(environment, dataset_name="Nhân viên")
    assert response.status_code == 422, response.text
    assert not environment.store.writes
    with Session(environment.engine) as session:
        assert not session.scalars(select(Dataset)).all()


def test_openapi_explains_both_choices_and_only_requires_file():
    spec = FastAPI()
    spec.include_router(router)
    schema = spec.openapi()
    operation = schema["paths"]["/ingestion/uploads"]["post"]
    reference = operation["requestBody"]["content"]["multipart/form-data"]["schema"]["$ref"]
    form = schema["components"]["schemas"][reference.rsplit("/", 1)[1]]
    assert form["required"] == ["file"]
    assert {"dataset_name", "description", "domain", "dataset_id"} <= form["properties"].keys()
    assert "dataset_name" in operation["description"]
