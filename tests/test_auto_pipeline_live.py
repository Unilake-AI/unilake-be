"""Toàn luồng thật: API tải file → PostgreSQL → MinIO Bronze → Silver Worker → Parquet.

Mỗi phép thử tạo database riêng và chỉ dọn object thuộc chính lượt thử đó.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import psycopg
import pytest
from dotenv import dotenv_values
from fastapi import FastAPI
from psycopg import sql
from sqlalchemy import URL, create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.deps import get_current_user
from app.core.exceptions import AppException, app_exception_handler
from app.dependencies import get_db
from app.infra.minio.bronze import client as admin_storage
from app.modules.ingestion.router import router as ingestion_router
from app.modules.ingestion.services.worker import WorkerState
from app.modules.ingestion.services.worker import run_once as ingest_once
from app.modules.pipeline.repositories.auto_pipeline import AutoPipelineRepository
from app.modules.pipeline.repositories.identity import CrosswalkRepository
from app.modules.pipeline.repositories.management_db import management_engine
from app.modules.pipeline.repositories.silver import SilverRepository
from app.modules.pipeline.repositories.silver_storage import MinioSilverStore
from app.modules.pipeline.routers.auto_pipeline import router as auto_router
from app.modules.pipeline.services import silver_worker
from app.modules.pipeline.services.source_cleaning import run_manual_cleaning
from app.modules.pipeline.services.source_profiles import SourceProfile
from app.modules.pipeline.silver.publication import SilverPublicationService
from app.providers import silver_writer_client
from tests.test_source_cleaning import definition

pytestmark = pytest.mark.skipif(
    os.getenv("AUTO_PIPELINE_LIVE_TEST") != "1", reason="Cần bật kiểm thử PostgreSQL và MinIO local"
)
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def system(monkeypatch):
    assert settings.db_host in {"localhost", "127.0.0.1", "::1"}, (
        "Chỉ kiểm thử trên PostgreSQL local"
    )
    # Bộ kiểm thử chạy cả hai worker; chỉ nạp thêm tài khoản đọc staging của worker,
    # không mở rộng quyền này cho API chạy thông thường.
    worker_values = dotenv_values(".env.upload-worker.local")
    monkeypatch.setattr(
        settings,
        "upload_reader_access_key",
        settings.upload_reader_access_key or worker_values.get("UPLOAD_READER_ACCESS_KEY") or "",
    )
    monkeypatch.setattr(
        settings,
        "upload_reader_secret_key",
        settings.upload_reader_secret_key or worker_values.get("UPLOAD_READER_SECRET_KEY") or "",
    )
    name = "unilake_auto_test_" + uuid4().hex
    connection_options = {
        "host": settings.db_host,
        "port": settings.db_port,
        "user": settings.db_user,
        "password": settings.db_password,
    }
    admin = psycopg.connect(dbname=settings.db_name, autocommit=True, **connection_options)
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = URL.create(
        "postgresql+psycopg",
        username=settings.db_user,
        password=settings.db_password,
        host=settings.db_host,
        port=settings.db_port,
        database=name,
    )
    engine = create_engine(url, pool_pre_ping=True)
    async_engine = create_async_engine(url.set(drivername="postgresql+asyncpg"), poolclass=NullPool)
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    monkeypatch.setattr(settings, "app_env", "test")
    scope = "auto_" + uuid4().hex
    extra_scopes = []
    try:
        with psycopg.connect(dbname=name, autocommit=True, **connection_options) as connection:
            connection.execute((ROOT / "infra/postgres/init/02-schema.sql").read_bytes())
            for migration in sorted((ROOT / "infra/postgres/migrations").glob("*.sql")):
                connection.execute(migration.read_bytes())
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO app.source_system(source_system_id,source_code,source_name,source_type) OVERRIDING SYSTEM VALUE VALUES (17,'AUTO_TEST','Auto test','CSV')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO app.data_layer(data_layer_id,layer_code,layer_name) OVERRIDING SYSTEM VALUE VALUES (1,'BRONZE','Bronze') ON CONFLICT (layer_code) DO NOTHING"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO app.dataset(dataset_id,dataset_code,dataset_name,domain,source_system_id,data_layer_id) OVERRIDING SYSTEM VALUE VALUES (31,'auto_test','Auto test','hr',17,1)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO app.data_source(data_source_id,source_system_id,source_name,source_type,connection_type,configuration) OVERRIDING SYSTEM VALUE VALUES (21,17,'UPLOAD_31_CSV','CSV','UPLOAD',CAST(:configuration AS jsonb))"
                ),
                {"configuration": json.dumps({"upload_dataset_id": 31})},
            )
            # Tránh trùng đường dẫn Bronze với job của database người dùng đang chạy.
            connection.execute(
                text(
                    "SELECT setval(pg_get_serial_sequence('app.ingestion_job','ingestion_job_id'),:start)"
                ),
                {"start": int(uuid4().hex[:8], 16) + 10000000},
            )
            cw = CrosswalkRepository(connection)
            connection.execute(
                text(
                    "INSERT INTO app.canonical_identity(canonical_id,entity) OVERRIDING SYSTEM VALUE VALUES (7009,'position')"
                )
            )
            cw.register(
                namespace="17",
                entity="position",
                source=("007",),
                canonical_id=7009,
                evidence="Đã đối chiếu trong kiểm thử",
                valid_from=datetime(2020, 1, 1, tzinfo=UTC),
            )
            cw.freeze("source-17-v1")
        payload = definition()
        payload.update(scope=scope)
        profile = SourceProfile.model_validate(payload)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO app.silver_source_profile(profile_id,definition,fingerprint,revision,updated_by,updated_at) VALUES ('live',CAST(:definition AS jsonb),:fingerprint,1,'live-admin',now())"
                ),
                {
                    "definition": json.dumps(profile.model_dump(mode="json")),
                    "fingerprint": profile.fingerprint,
                },
            )
        app = FastAPI()
        app.add_exception_handler(AppException, app_exception_handler)
        app.include_router(ingestion_router)
        app.include_router(auto_router)
        user = {"sub": "live-admin", "roles": ["ADMIN"]}
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[management_engine] = lambda: engine

        async def db_override():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db] = db_override

        async def request(method, path, **kwargs):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as http:
                return await http.request(method, path, **kwargs)

        def upload(content=None):
            body = (
                content
                or "position_id,position_code,position_name,position_level\n007,NV,Chuyên viên,Nhân viên\n"
            )
            response = asyncio.run(
                request(
                    "POST",
                    "/ingestion/uploads",
                    data={"dataset_id": "31"},
                    files={"file": ("positions.csv", body.encode("utf-8"), "text/csv")},
                )
            )
            assert response.status_code == 202, response.text
            return response.json()["ingestion_job_id"]

        def ingestion():
            assert asyncio.run(ingest_once(WorkerState(), factory))
            with engine.connect() as connection:
                states = connection.execute(
                    text(
                        "SELECT status,error_message,validation_report FROM app.ingestion_job ORDER BY ingestion_job_id"
                    )
                ).all()
            assert all(row[0] == "SUCCESS" for row in states), [
                (row[0], row[1], (row[2] or {}).get("issues")) for row in states
            ]

        def policy(**changes):
            args = {
                "expected_revision": 0,
                "enabled": True,
                "profile_id": "live",
                "allow_partial_publication": False,
                "max_attempts": 3,
                "retry_delay_seconds": 1,
                **changes,
            }
            response = asyncio.run(request("PUT", "/pipeline/auto/datasets/31", json=args))
            assert response.status_code == 200, response.text
            return response.json()

        publisher = SilverPublicationService(
            repository=SilverRepository(engine),
            storage=MinioSilverStore(silver_writer_client(), settings.minio_bucket_silver),
            contracts=profile.contracts,
            scope=scope,
        )
        yield {
            "app": app,
            "engine": engine,
            "upload": upload,
            "ingestion": ingestion,
            "policy": policy,
            "request": request,
            "publisher": publisher,
            "user": user,
            "extra_scopes": extra_scopes,
        }
    finally:
        # Chỉ xóa đúng phiên bản file của các job trong database kiểm thử này.
        storage = admin_storage()
        with engine.connect() as connection:
            jobs = connection.execute(
                text("SELECT upload_input,output,bronze_output FROM app.ingestion_job")
            ).all()
            runs = connection.execute(text("SELECT run_id FROM app.cleaning_run")).scalars().all()
        for row in jobs:
            for evidence in row:
                if (
                    isinstance(evidence, dict)
                    and evidence.get("uri")
                    and evidence.get("version_id")
                ):
                    uri = urlsplit(evidence["uri"])
                    storage.remove_object(
                        uri.netloc, uri.path.lstrip("/"), version_id=evidence["version_id"]
                    )
        prefixes = [f"silver/reports/{run}/" for run in runs]
        prefixes.append(
            "silver/production/" + hashlib.sha256(f"production:{scope}".encode()).hexdigest() + "/"
        )
        prefixes.extend(
            "silver/production/" + hashlib.sha256(f"production:{item}".encode()).hexdigest() + "/"
            for item in extra_scopes
        )
        for prefix in prefixes:
            for item in storage.list_objects(
                settings.minio_bucket_silver, prefix=prefix, recursive=True, include_version=True
            ):
                if item.object_name:
                    storage.remove_object(
                        settings.minio_bucket_silver, item.object_name, version_id=item.version_id
                    )
        asyncio.run(async_engine.dispose())
        engine.dispose()
        admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
        admin.close()


def test_manual_setup_reads_real_bronze_and_saves_only_after_preview(system):
    from app.modules.pipeline.routers.cleaning_setup import router as setup_router

    system["app"].include_router(setup_router)
    job_id = system["upload"](
        "position_id,position_code,position_name,position_level\n007,NV,Chuyên viên,Staff\n"
    )
    system["ingestion"]()
    path = f"/pipeline/cleaning/ingestion-jobs/{job_id}/setup"
    analysis = asyncio.run(system["request"]("GET", path))
    assert analysis.status_code == 200, analysis.text
    assert analysis.json()["suggested_entity"] == "position"
    proposal = next(item for item in analysis.json()["entities"] if item["entity"] == "position")
    choices = {
        "analysis_fingerprint": analysis.json()["analysis_fingerprint"],
        "entity": "position",
        "field_mapping": proposal["field_mapping"],
        "crosswalk_release": "source-17-v1",
        "identity_mode": "canonical",
    }
    response = asyncio.run(system["request"]("POST", f"{path}/preview", json=choices))
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["records_normalized"] == 1
    assert preview["cleaned_sample"][0]["position_id"] == "007"
    assert preview["normalized_sample"][0]["position_id"] == 7009
    confirm = asyncio.run(
        system["request"](
            "POST",
            f"{path}/confirm",
            json={
                "choices": choices,
                "confirmation_token": preview["confirmation_token"],
            },
        )
    )
    assert confirm.status_code == 200, confirm.text
    assert confirm.json()["definition"]["dataset_id"] == 31
    with system["engine"].connect() as connection:
        # Xem trước và lưu lựa chọn không tự chạy Silver, không bật lịch tự động.
        assert connection.execute(text("SELECT count(*) FROM app.cleaning_run")).scalar_one() == 0
        assert (
            connection.execute(text("SELECT count(*) FROM app.silver_snapshot")).scalar_one() == 0
        )
        assert (
            connection.execute(text("SELECT count(*) FROM app.auto_pipeline_policy")).scalar_one()
            == 0
        )


@pytest.mark.parametrize("kind", ["known", "generic", "partial"])
def test_source_mode_upload_preview_confirm_to_real_parquet_without_crosswalk(system, kind):
    from app.modules.pipeline.routers.cleaning_setup import router as setup_router
    from app.modules.pipeline.routers.snapshots import publisher_for

    system["app"].include_router(setup_router)
    source_id = int(uuid4().hex[:12], 16)
    with system["engine"].begin() as connection:
        # Tạo phạm vi file kiểm thử riêng, không dùng chung đường dẫn Silver với dữ liệu đang vận hành.
        connection.execute(
            text("ALTER TABLE app.data_source ALTER COLUMN data_source_id SET GENERATED BY DEFAULT")
        )
        connection.execute(
            text("UPDATE app.data_source SET data_source_id=:id WHERE data_source_id=21"),
            {"id": source_id},
        )
        connection.execute(text("DELETE FROM app.crosswalk_release"))
    body = "position_id,position_code,position_name,position_level\n007,NV,Chuyên viên,Staff\n7,CV,Người khác,Staff\n"
    if kind == "generic":
        body = "tracking_code,amount,note\n007,12,\n7,15,ghi chú\n"
    if kind == "partial":
        body += "008,,Thiếu mã chức vụ,Staff\n"
    job_id = system["upload"](body)
    system["ingestion"]()
    path = f"/pipeline/cleaning/ingestion-jobs/{job_id}/setup"
    analysis_response = asyncio.run(system["request"]("GET", path))
    assert analysis_response.status_code == 200, analysis_response.text
    analysis = analysis_response.json()
    assert analysis["crosswalk_releases"] == []
    entity = "file_31" if kind == "generic" else "position"
    proposal = next(item for item in analysis["entities"] if item["entity"] == entity)
    choices = {
        "analysis_fingerprint": analysis["analysis_fingerprint"],
        "entity": entity,
        "field_mapping": proposal["field_mapping"],
    }
    if kind == "generic":
        choices.update(
            field_types={"tracking_code": "string", "amount": "integer", "note": "string"},
            identifier_fields=["tracking_code"],
        )
    response = asyncio.run(system["request"]("POST", f"{path}/preview", json=choices))
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["records_normalized"] == 2, preview
    confirm = asyncio.run(
        system["request"](
            "POST",
            f"{path}/confirm",
            json={"choices": choices, "confirmation_token": preview["confirmation_token"]},
        )
    )
    assert confirm.status_code == 200, confirm.text
    saved = confirm.json()
    system["extra_scopes"].append(saved["definition"]["scope"])
    assert saved["definition"]["identity_mode"] == "source"
    assert saved["definition"]["crosswalk_release"] is None
    result = run_manual_cleaning(
        job_id,
        requested_by="live-admin",
        profile_id=saved["profile_id"],
        engine_override=system["engine"],
        allow_partial_publication=kind == "partial",
    )
    assert result["status"] == (
        "PUBLISHED_WITH_QUARANTINE" if kind == "partial" else "PUBLISHED"
    ), result
    publisher = publisher_for(system["engine"], saved["profile_id"])
    tables = publisher.read_snapshot(result["publication"]["snapshot_id"])
    key = "tracking_code" if kind == "generic" else "position_id"
    assert [row[key] for row in tables[entity]] == ["007", "7"]
    manifest = publisher.describe_snapshot(result["publication"]["snapshot_id"])
    assert manifest["entities"][entity]["lineage"][0]["crosswalk_version"] is None
    assert manifest["entities"][entity]["lineage"][0]["record_locators"]
    if kind == "generic":
        assert tables[entity][0]["amount"] == 12
        assert tables[entity][0]["note"] is None
    with system["engine"].connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM app.quarantine_record")
        ).scalar_one() == (1 if kind == "partial" else 0)
    if kind == "known":
        # Lần nạp đầu nay đã tự xếp hàng dù chưa có policy; xử lý xong trước khi thử override.
        from app.modules.pipeline.services.cleaning_setup import automatic_source_profile

        system["extra_scopes"].append(automatic_source_profile(system["engine"], job_id).scope)
        assert silver_worker.run_once(system["engine"], "default-before-override")
        assert _job(system)["status"] == "SUCCESS"
        # Cùng cấu hình giữ mã nguồn cũng chạy được qua hàng đợi và Silver Worker.
        system["policy"](profile_id=saved["profile_id"])
        system["upload"](body)
        system["ingestion"]()
        assert silver_worker.run_once(system["engine"], "source-mode-worker")
        assert _job(system)["status"] == "SUCCESS"


def _job(system):
    with system["engine"].connect() as connection:
        return dict(
            connection.execute(
                text("SELECT * FROM app.auto_pipeline_job ORDER BY created_at DESC LIMIT 1")
            )
            .mappings()
            .one()
        )


@pytest.mark.parametrize("mode", ["success", "mixed", "all_bad", "transient", "after_commit"])
def test_upload_to_auto_silver_and_recovery(system, monkeypatch, mode):
    system["policy"](allow_partial_publication=mode == "mixed")
    body = "position_id,position_code,position_name,position_level\n007,NV,Chuyên viên,Nhân viên\n"
    if mode == "mixed":
        body += "008,,Thiếu mã,Nhân viên\n"
    if mode == "all_bad":
        body = "position_id,position_code,position_name,position_level\n007,,Thiếu mã,Nhân viên\n"
    ingestion_id = system["upload"](body)
    with system["engine"].connect() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM app.auto_pipeline_job")).scalar_one() == 0
        )
    system["ingestion"]()
    job = _job(system)
    assert job["ingestion_job_id"] == ingestion_id
    assert job["status"] == "QUEUED"
    if mode == "transient":

        def timeout(*_args, **_kwargs):
            raise TimeoutError("Không in nội dung riêng tư")

        monkeypatch.setattr(silver_worker, "run_manual_cleaning", timeout)
        assert silver_worker.run_once(system["engine"], "test-worker")
        assert _job(system)["status"] == "RETRY_WAIT"
        assert not silver_worker.run_once(system["engine"], "test-worker")
        with system["engine"].begin() as connection:
            connection.execute(
                text("UPDATE app.auto_pipeline_job SET next_attempt_at=now()-interval '1 second'")
            )
        monkeypatch.setattr(silver_worker, "run_manual_cleaning", run_manual_cleaning)
    if mode == "after_commit":

        def fail_ack(*_args, **_kwargs):
            raise TimeoutError("Worker dừng sau khi công bố")

        monkeypatch.setattr(AutoPipelineRepository, "finish", fail_ack)
    assert silver_worker.run_once(system["engine"], "test-worker")
    job = _job(system)
    assert job["status"] == ("QUARANTINED" if mode == "all_bad" else "SUCCESS"), job
    assert not silver_worker.run_once(system["engine"], "test-worker")
    with system["engine"].connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM app.auto_pipeline_attempt")
        ).scalar_one() == (2 if mode == "transient" else 1)
        if mode == "all_bad":
            assert (
                connection.execute(text("SELECT count(*) FROM app.silver_snapshot")).scalar_one()
                == 0
            )
        else:
            assert (
                connection.execute(text("SELECT count(*) FROM app.silver_snapshot")).scalar_one()
                == 1
            )
            assert job["result"]["publication"]["scope"] == system["publisher"].scope
            assert job["result"]["publication"]["mode"] == "production"
            [record] = system["publisher"].read_active()["position"]
            assert record["position_id"] == 7009
        if mode in {"mixed", "all_bad"}:
            assert (
                connection.execute(text("SELECT count(*) FROM app.quarantine_record")).scalar_one()
                == 1
            )
    response = asyncio.run(system["request"]("GET", f"/pipeline/auto/jobs/{job['job_id']}"))
    assert response.status_code == 200
    assert "lease_token" not in response.json()
    system["user"]["roles"] = ["ACADEMIC_STAFF"]
    assert (
        asyncio.run(system["request"]("GET", f"/pipeline/auto/jobs/{job['job_id']}")).status_code
        == 404
    )


def test_disable_config_and_expired_lease_fencing(system):
    system["policy"]()
    system["upload"]()
    system["ingestion"]()
    repo = AutoPipelineRepository(system["engine"])
    old = repo.claim("old-worker", 120)
    assert old is not None
    with system["engine"].begin() as connection:
        connection.execute(
            text("UPDATE app.auto_pipeline_job SET lease_expires_at=now()-interval '1 second'")
        )
    current = repo.claim("new-worker", 120)
    assert current is not None
    assert not repo.heartbeat("old-worker", old, 120)
    with (
        system["engine"].begin() as connection,
        pytest.raises(ValueError, match="AUTO_PIPELINE_LEASE_LOST"),
    ):
        repo.published(connection, old["job_id"], old["lease_token"], {})
    repo.fail(current, "AUTO_PIPELINE_INFRASTRUCTURE_TRANSIENT", True)
    system["policy"](enabled=False, expected_revision=1)
    assert repo.claim("next-worker", 120) is None
    assert _job(system)["status"] == "CANCELLED"


def test_multiple_workers_cannot_claim_same_dataset(system):
    system["policy"]()
    for _ in range(2):
        system["upload"]()
        system["ingestion"]()
    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = list(
            pool.map(
                lambda worker: AutoPipelineRepository(system["engine"]).claim(worker, 120),
                ["a", "b"],
            )
        )
    assert sum(job is not None for job in claimed) == 1


def test_retry_budget_and_manual_retry_keep_attempt_history(system, monkeypatch):
    system["policy"]()
    system["upload"]()
    system["ingestion"]()
    job = _job(system)

    def timeout(*_args, **_kwargs):
        raise TimeoutError("Lỗi hạ tầng giả lập")

    monkeypatch.setattr(silver_worker, "run_manual_cleaning", timeout)
    for attempt in range(1, 4):
        assert silver_worker.run_once(system["engine"], "retry-worker")
        job = _job(system)
        assert job["attempt_number"] == attempt
        assert job["status"] == ("FAILED" if attempt == 3 else "RETRY_WAIT")
        if attempt < 3:
            with system["engine"].begin() as connection:
                connection.execute(
                    text(
                        "UPDATE app.auto_pipeline_job SET next_attempt_at=now()-interval '1 second'"
                    )
                )
    assert not silver_worker.run_once(system["engine"], "retry-worker")
    response = asyncio.run(system["request"]("POST", f"/pipeline/auto/jobs/{job['job_id']}/retry"))
    assert response.status_code == 200, response.text
    monkeypatch.setattr(silver_worker, "run_manual_cleaning", run_manual_cleaning)
    assert silver_worker.run_once(system["engine"], "retry-worker")
    assert _job(system)["status"] == "SUCCESS"
    response = asyncio.run(
        system["request"]("GET", f"/pipeline/auto/jobs/{job['job_id']}/attempts")
    )
    assert [entry["status"] for entry in response.json()] == [
        "FAILED",
        "FAILED",
        "FAILED",
        "SUCCESS",
    ]


def test_enqueue_is_transactional_and_disabled_policy_does_not_enqueue(system):
    system["policy"]()
    job_id = system["upload"]()
    with system["engine"].connect() as connection:
        transaction = connection.begin()
        connection.execute(
            text(
                "UPDATE app.ingestion_job SET status='SUCCESS',output=jsonb_build_object('test',true) WHERE ingestion_job_id=:id"
            ),
            {"id": job_id},
        )
        assert (
            connection.execute(text("SELECT count(*) FROM app.auto_pipeline_job")).scalar_one() == 1
        )
        transaction.rollback()
    with system["engine"].connect() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM app.auto_pipeline_job")).scalar_one() == 0
        )
    system["policy"](enabled=False, expected_revision=1)
    system["ingestion"]()
    with system["engine"].connect() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM app.auto_pipeline_job")).scalar_one() == 0
        )


def test_separate_worker_process_consumes_real_queue(system):
    import subprocess
    import sys
    import time

    system["policy"]()
    system["upload"]()
    system["ingestion"]()
    process = subprocess.Popen(
        [sys.executable, "-m", "app.silver_worker"],
        env={
            **os.environ,
            "DB_NAME": system["engine"].url.database,
            "ENVIRONMENT": "test",
            "DB_HOST": settings.db_host,
            "DB_PORT": str(settings.db_port),
            "SILVER_WORKER_ID": "live-process",
            "SILVER_POLL_SECONDS": "1",
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            status = _job(system)["status"]
            if status in {"SUCCESS", "FAILED", "QUARANTINED"}:
                break
            if process.poll() is not None:
                assert process.stderr is not None
                pytest.fail(process.stderr.read())
            time.sleep(0.05)
        assert _job(system)["status"] == "SUCCESS"
        assert system["publisher"].read_active()["position"][0]["position_id"] == 7009
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        if process.stderr:
            process.stderr.close()


def test_can_disable_existing_policy_after_source_is_deactivated(system):
    system["policy"]()
    with system["engine"].begin() as connection:
        connection.execute(
            text("UPDATE app.data_source SET is_active=false WHERE data_source_id=21")
        )
    policy = system["policy"](enabled=False, expected_revision=1)
    assert policy["enabled"] is False
    assert policy["revision"] == 2
