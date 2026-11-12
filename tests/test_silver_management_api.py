"""Kiểm tra API cấu hình bền vững, quyền truy cập và chọn phiên bản Silver."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Table, create_engine, insert, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.schema import CreateTable

from app.core.config import settings
from app.core.deps import get_current_user
from app.core.exceptions import AppException, app_exception_handler
from app.dependencies import get_db
from app.main import app as application
from app.modules.datasources import datasets_router
from app.modules.datasources.models import DataLayer, Dataset, DataSource, SourceSystem
from app.modules.ingestion.contracts import FileProvenance
from app.modules.pipeline.models.profiles import SilverActivation, SilverSourceProfile
from app.modules.pipeline.repositories.silver import SILVER_TABLES, SilverRepository
from app.modules.pipeline.repositories.silver_storage import LocalSilverStore
from app.modules.pipeline.routers import profiles as profiles_router
from app.modules.pipeline.routers import snapshots as snapshots_router
from app.modules.pipeline.silver.publication import SilverPublicationService
from app.repositories.silver_profile_repo import load_source_profile
from tests.test_silver_publication import normalized
from tests.test_source_cleaning import definition


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


@pytest.fixture
def environment(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'management.db'}",
        execution_options={"schema_translate_map": {"app": None}},
    )
    for model in (SourceSystem, DataLayer, Dataset, DataSource, SilverSourceProfile):
        table = cast("Table", model.__table__)
        if model is DataSource:
            # SQLite dùng bản tương đương của giá trị mặc định JSON PostgreSQL.
            ddl = str(CreateTable(table).compile(engine))
            with engine.begin() as connection:
                connection.exec_driver_sql(ddl.replace("'{}'::jsonb", "'{}'").replace("app.", ""))
        else:
            table.create(engine)
    for table in SILVER_TABLES:
        table.create(engine)
    cast("Table", SilverActivation.__table__).create(engine)
    with engine.begin() as connection:
        connection.execute(
            insert(SourceSystem).values(
                source_system_id=17,
                source_code="HR",
                source_name="Nhân sự",
                source_type="FILE",
                is_active=True,
            )
        )
        connection.execute(
            insert(DataLayer).values(data_layer_id=1, layer_code="BRONZE", layer_name="Bronze")
        )
        connection.execute(
            insert(Dataset).values(
                dataset_id=31,
                dataset_code="positions",
                dataset_name="Chức vụ",
                domain="hr",
                source_system_id=17,
                data_layer_id=1,
                status="ACTIVE",
            )
        )
        connection.execute(
            insert(DataSource).values(
                data_source_id=21,
                source_system_id=17,
                source_name="positions",
                source_type="CSV",
                is_active=True,
            )
        )
    profile_path = tmp_path / "profiles.json"
    profile_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(settings, "silver_profiles_path", str(profile_path))
    app = FastAPI()
    app.add_exception_handler(AppException, app_exception_handler)
    app.include_router(profiles_router.router)
    app.include_router(snapshots_router.router)
    app.include_router(datasets_router.router)
    monkeypatch.setattr(
        "app.modules.pipeline.repositories.management_db.create_engine", lambda *_args, **_kwargs: engine
    )
    user = {"sub": "1", "roles": ["DATA_ADMIN"]}
    app.dependency_overrides[get_current_user] = lambda: user
    yield TestClient(app), engine, user
    engine.dispose()


def create_profile(client):
    response = client.post(
        "/pipeline/profiles", json={"profile_id": "positions", "definition": definition()}
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_profiles_persist_and_reject_stale_edits(environment):
    client, engine, _ = environment
    created = create_profile(client)
    assert created["revision"] == 1
    assert client.get("/pipeline/profiles").json()[0]["profile_id"] == "positions"
    assert (
        load_source_profile(engine, settings.silver_profiles_path, "positions").fingerprint
        == created["fingerprint"]
    )
    assert (
        client.post(
            "/pipeline/profiles", json={"profile_id": "positions", "definition": definition()}
        ).status_code
        == 409
    )
    changed = definition()
    changed["collapse_exact_duplicates"] = True
    update = {"expected_revision": 1, "definition": changed}
    response = client.put("/pipeline/profiles/positions", json=update)
    assert response.status_code == 200
    assert response.json()["revision"] == 2
    assert response.json()["fingerprint"] != created["fingerprint"]
    assert client.put("/pipeline/profiles/positions", json=update).status_code == 409


def test_profiles_validate_source_and_support_file_fallback(environment):
    client, _, _ = environment
    invalid = definition()
    invalid["source_system_id"] = 999
    assert (
        client.post(
            "/pipeline/profiles", json={"profile_id": "invalid", "definition": invalid}
        ).status_code
        == 422
    )
    with open(settings.silver_profiles_path, "w", encoding="utf-8") as stream:
        json.dump({"positions": definition()}, stream)
    assert client.get("/pipeline/profiles/positions").json()["storage"] == "file"
    response = client.put(
        "/pipeline/profiles/positions", json={"expected_revision": 0, "definition": definition()}
    )
    assert response.status_code == 200
    assert response.json()["storage"] == "database"


@pytest.mark.parametrize("role", ["HR_STAFF", "VIEWER"])
def test_non_admin_cannot_manage_silver(environment, role):
    client, _, user = environment
    user["roles"] = [role]
    assert client.get("/pipeline/profiles").status_code == 403
    assert (
        client.get("/pipeline/silver/snapshots", params={"profile_id": "positions"}).status_code
        == 403
    )
    assert (
        client.post(
            "/datasets",
            json={"dataset_code": "x", "dataset_name": "X", "domain": "hr", "source_system_id": 17},
        ).status_code
        == 403
    )


def test_snapshot_read_and_activation_are_verified_and_audited(environment, tmp_path, monkeypatch):
    client, engine, _ = environment
    create_profile(client)
    profile = load_source_profile(engine, settings.silver_profiles_path, "positions")
    store = LocalSilverStore(tmp_path / "silver")
    monkeypatch.setattr(snapshots_router, "silver_writer_client", lambda: None)
    monkeypatch.setattr(snapshots_router, "MinioSilverStore", lambda *_: store)
    publisher = SilverPublicationService(
        repository=SilverRepository(engine),
        storage=store,
        contracts=profile.contracts,
        scope=profile.scope,
    )
    result = normalized()
    provenance = FileProvenance(
        "SYNTH",
        "1",
        "positions",
        "CSV",
        "s3://bronze/input.csv",
        "v1",
        "0" * 64,
        "1" * 64,
        "2" * 64,
        "2026-01-01T00:00:00Z",
        "parsed_record_ordinal",
    )
    result = replace(result, provenance=provenance, input_version=provenance.input_version)
    first = publisher.publish((result,))
    second = publisher.publish((result,), base_snapshot=first.snapshot_id)
    params = {"profile_id": "positions"}
    assert len(client.get("/pipeline/silver/snapshots", params=params).json()["items"]) == 2
    data = client.get(
        f"/pipeline/silver/snapshots/{first.snapshot_id}/data",
        params={**params, "entity": "position", "limit": 1},
    )
    assert data.status_code == 200, data.text
    assert len(data.json()["items"]) == 1
    target = f"/pipeline/silver/snapshots/{first.snapshot_id}/activate"
    response = client.post(
        target, params=params, json={"expected_current_snapshot": second.snapshot_id}
    )
    assert response.status_code == 200, response.text
    assert (
        client.get("/pipeline/silver/snapshots/active", params=params).json()["snapshot_id"]
        == first.snapshot_id
    )
    assert (
        client.post(
            target, params=params, json={"expected_current_snapshot": second.snapshot_id}
        ).status_code
        == 409
    )
    with engine.connect() as connection:
        row = connection.execute(select(SilverActivation)).mappings().one()
        assert row["previous_snapshot_id"] == second.snapshot_id
        assert row["activated_by"] == "1"
    # File đã bị thay đổi không được dùng để chọn phiên bản hiện hành.
    object_path = next((tmp_path / "silver").rglob("*manifest*.json"))
    object_path.write_text("{}", encoding="utf-8")
    bad_id = first.snapshot_id if first.snapshot_id in str(object_path) else second.snapshot_id
    assert (
        client.post(
            f"/pipeline/silver/snapshots/{bad_id}/activate",
            params=params,
            json={"expected_current_snapshot": first.snapshot_id},
        ).status_code
        == 422
    )


def test_staff_dataset_list_is_scoped(environment):
    client, _, user = environment
    user["roles"] = ["HR_STAFF"]
    db = AsyncMock()
    db.scalars.return_value = []
    client.app.dependency_overrides[get_db] = lambda: db
    assert client.get("/datasets").status_code == 200
    query = db.scalars.call_args.args[0]
    assert ["hr"] in query.compile().params.values()
    user["roles"] = ["VIEWER"]
    assert client.get("/datasets").status_code == 403


def test_dataset_creation_validates_source_and_selects_bronze(environment):
    client, _, _ = environment
    db = AsyncMock()
    db.get.return_value = SourceSystem(source_system_id=17, is_active=True)
    db.scalar.return_value = DataLayer(data_layer_id=1, layer_code="BRONZE")

    def add(dataset):
        dataset.dataset_id = 32
        dataset.created_at = datetime.now(UTC)

    db.add = Mock(side_effect=add)
    client.app.dependency_overrides[get_db] = lambda: db
    payload = {
        "dataset_code": "employees",
        "dataset_name": "Nhân sự",
        "domain": "hr",
        "source_system_id": 17,
    }
    response = client.post("/datasets", json=payload)
    assert response.status_code == 201, response.text
    assert response.json()["data_layer_id"] == 1
    assert response.json()["status"] == "ACTIVE"
    db.get.return_value = None
    assert client.post("/datasets", json=payload).status_code == 422


def test_swagger_includes_silver_management_routes():
    paths = application.openapi()["paths"]
    for path in (
        "/api/v1/datasets",
        "/api/v1/source-systems",
        "/api/v1/pipeline/profiles",
        "/api/v1/pipeline/profiles/{profile_id}",
        "/api/v1/pipeline/cleaning/ingestion-jobs/{ingestion_job_id}/preview",
        "/api/v1/pipeline/silver/snapshots",
        "/api/v1/pipeline/silver/snapshots/active",
        "/api/v1/pipeline/silver/snapshots/{snapshot_id}/data",
        "/api/v1/pipeline/silver/snapshots/{snapshot_id}/activate",
    ):
        assert path in paths
