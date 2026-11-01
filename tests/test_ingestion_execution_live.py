"""API/worker thật bằng SA runtime; schema và prefix UUID riêng cho dữ liệu kiểm thử."""

from __future__ import annotations

import asyncio
import io
import json
import os
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import asyncpg
import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi import FastAPI
from openpyxl import Workbook
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import ingestion_worker
from app.core.config import settings
from app.core.deps import get_current_user
from app.core.exceptions import AppException, app_exception_handler
from app.dependencies import get_db
from app.infra.minio.bronze import client
from app.infra.minio.client import bronze_reader_client, bronze_verifier_client
from app.modules.datasources.models import DataSource
from app.modules.ingestion.models import IngestionJob
from app.modules.ingestion.router import router
from app.repositories.ingestion_repo import IngestionRepository, utcnow

pytestmark = pytest.mark.skipif(
    os.getenv("INGESTION_LIVE_TEST") != "1", reason="Requires PostgreSQL and MinIO"
)


def _csv_validation_policy() -> dict[str, object]:
    """Policy dùng trong API live test; ID catalog của fixture luôn bắt đầu từ 1."""
    return {
        "policy_id": "live-csv-v1",
        "version": "1",
        "source_system_id": 1,
        "data_source_id": 1,
        "dataset_id": 1,
        "parser": {
            "kind": "CSV",
            "delimiter": ",",
            "encoding": "utf-8",
            "has_header": True,
            "quote_char": '"',
            "skip_rows": 0,
        },
        "columns": [
            {"name": "id", "required": True},
            {"name": "name", "required": True},
        ],
        "column_order": "any",
        "allow_extra_columns": False,
        "allow_empty_records": False,
        "limits": {
            "max_file_bytes": 1_000_000,
            "max_columns": 10,
            "max_records": 1_000,
            "max_cell_chars": 10_000,
            "timeout_seconds": 30,
            "memory_limit_bytes": 10_000_000,
        },
    }


def _validation_policy(
    *,
    source_type: str,
    parser: dict[str, object],
    columns: list[dict[str, object]],
) -> dict[str, object]:
    """Các ID khớp fixture catalog riêng của từng test live."""
    return {
        "policy_id": f"live-{source_type.lower()}-v1",
        "version": "1",
        "source_system_id": 1,
        "data_source_id": 1,
        "dataset_id": 1,
        "parser": {"kind": source_type, **parser},
        "columns": columns,
        "column_order": "any",
        "allow_extra_columns": False,
        "allow_empty_records": False,
        "limits": {
            "max_file_bytes": 1_000_000,
            "max_columns": 10,
            "max_records": 1_000,
            "max_cell_chars": 10_000,
            "timeout_seconds": 30,
            "memory_limit_bytes": 10_000_000,
        },
    }


def _test_app(factory, user: dict[str, object]) -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(AppException, app_exception_handler)
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: user

    async def db_override():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = db_override
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", ["admissions", "academic", "hr"])
async def test_api_queue_worker_storage_failures_and_recovery(tmp_path, monkeypatch, domain):
    suffix = uuid4().hex
    schema, bucket = "ingestion_test_" + suffix, settings.minio_bucket_bronze
    # Policy SA chỉ cấp bucket Bronze thật; không nới policy để test bucket tùy ý.
    partition = f"{domain}-iam-{suffix}"
    root = Path(__file__).resolve().parents[1]
    ddl = (root / "infra/postgres/init/02-schema.sql").read_text(encoding="utf-8")
    ddl = ddl.replace("CREATE SCHEMA IF NOT EXISTS app;", f"CREATE SCHEMA {schema};")
    ddl = ddl.replace("SET search_path TO app, public;", f"SET search_path TO {schema}, public;")
    storage = client()
    engine = create_async_engine(
        settings.database_url,
        poolclass=NullPool,
        execution_options={"schema_translate_map": {"app": schema}},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(ingestion_worker, "async_session_factory", factory)
    path = tmp_path / "input.csv"
    path.write_bytes(b"id,name\r\n001,Example\r\n")
    conn = await asyncpg.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        database=settings.db_name,
    )
    try:
        await conn.execute(ddl)
        await conn.execute(
            "INSERT INTO source_system(source_code, source_name, source_type) VALUES ('TEST', 'Test', 'CSV')"
        )
        await conn.execute(
            "INSERT INTO data_layer(layer_code, layer_name) VALUES ('BRONZE', 'Bronze')"
        )
        await conn.execute(
            "INSERT INTO dataset(dataset_code,dataset_name,domain,source_system_id,data_layer_id) VALUES ('test','Test',$1,1,1)",
            partition,
        )
        await conn.execute(
            "INSERT INTO data_source(source_system_id,source_name,source_type,location,configuration) VALUES (1,'Test','CSV',$1,$2::jsonb)",
            str(path),
            '{"delimiter":",","encoding":"utf-8","has_header":true}',
        )
        app = FastAPI()
        app.add_exception_handler(AppException, app_exception_handler)
        app.include_router(router)
        user = {"sub": "test-user", "roles": ["ADMIN"]}
        app.dependency_overrides[get_current_user] = lambda: user

        async def db_override():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db] = db_override
        payload = {
            "data_source_id": 1,
            "dataset_id": 1,
            "source_table": "input.csv",
            "validation_policy": _csv_validation_policy(),
        }
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as api:
            user["roles"] = ["VIEWER"]
            assert (await api.post("/ingestion/jobs", json=payload)).status_code == 403
            user["roles"] = ["ADMIN"]
            assert (
                await api.post("/ingestion/jobs", json={**payload, "data_source_id": 999})
            ).status_code == 404
            responses = await asyncio.gather(
                *(api.post("/ingestion/jobs", json=payload) for _ in range(2))
            )
            assert sorted(r.status_code for r in responses) == [202, 409]
            job_id = next(r.json()["ingestion_job_id"] for r in responses if r.status_code == 202)
            # Worker thật, phiên riêng biệt, thực sự chụp/đọc/ghi/kiểm tra checksum.
            assert sorted(
                await asyncio.gather(ingestion_worker.run_once(), ingestion_worker.run_once())
            ) == [False, True]
            result = (await api.get(f"/ingestion/jobs/{job_id}")).json()
            assert result["status"] == "SUCCESS", result
            assert result["rows_read"] == 1
            assert result["rows_written"] == 1
            report = result["validation_report"]
            assert report["result"] == "PASS"
            assert report["scan_complete"] is True
            assert report["records_scanned"] == 1
            assert report["total_errors"] == 0
            assert all(check["status"] == "PASS" for check in report["checks"])
            assert result["output"]["version_id"]
            key = result["output"]["uri"].split(f"s3://{bucket}/", 1)[1]
            # Kiểm tra metadata thật trên đúng version đã được job công bố.
            stat = bronze_verifier_client().stat_object(
                bucket, key, version_id=result["output"]["version_id"]
            )
            assert stat.metadata is not None
            tags = {name.lower(): value for name, value in stat.metadata.items()}
            for name, value in result["output"]["metadata"].items():
                assert tags[f"x-amz-meta-{name}"] == value
            assert tags["x-amz-meta-source-system-id"] == "1"
            assert tags["x-amz-meta-batch-id"] == f"job-{job_id}"
            response = bronze_reader_client().get_object(
                bucket, key, version_id=result["output"]["version_id"]
            )
            try:
                assert response.read() == path.read_bytes()
            finally:
                response.close()
                response.release_conn()
            async with factory() as session:
                source = await session.get(DataSource, 1)
                assert source is not None
                assert source.ingestion_count == 1
                assert source.last_ingested_at is not None
            versions_before_rejection = sorted(
                (obj.object_name, obj.version_id)
                for obj in storage.list_objects(
                    bucket, prefix=partition + "/", recursive=True, include_version=True
                )
            )
            # Lỗi cấu trúc được dừng trước upload, nhưng report lỗi phải được lưu trong job.
            path.write_bytes(b"id,id\r\n001,Example\r\n")
            blocked_id = (await api.post("/ingestion/jobs", json=payload)).json()[
                "ingestion_job_id"
            ]
            assert await ingestion_worker.run_once()
            blocked = (await api.get(f"/ingestion/jobs/{blocked_id}")).json()
            assert blocked["status"] == "FAILED"
            assert blocked["error_message"] == "BRONZE_INPUT_VALIDATION_BLOCKED"
            assert blocked["validation_report"]["result"] == "FAIL"
            assert blocked["validation_report"]["issues"][-1]["code"] == "HEADER_DUPLICATE"
            versions_after_rejection = sorted(
                (obj.object_name, obj.version_id)
                for obj in storage.list_objects(
                    bucket, prefix=partition + "/", recursive=True, include_version=True
                )
            )
            assert versions_after_rejection == versions_before_rejection
            path.write_bytes(b"id,name\r\n001,Example\r\n")
            # Worker kiểm tra lại sự tồn tại/cấu hình nguồn; đầu vào thiếu được ghi nhận lỗi bền vững.
            path.unlink()
            failed_id = (await api.post("/ingestion/jobs", json=payload)).json()["ingestion_job_id"]
            assert await ingestion_worker.run_once()
            failed = (await api.get(f"/ingestion/jobs/{failed_id}")).json()
            assert failed["error_message"] == "INGESTION_READING_FAILED"
            # Giả lập worker dừng đột ngột; job hết hạn không thể công bố thành công hay gửi heartbeat.
            dead_id = (await api.post("/ingestion/jobs", json=payload)).json()["ingestion_job_id"]
            async with factory() as session:
                repo = IngestionRepository(session)
                claimed = await repo.claim()
                assert claimed is not None
                assert claimed.ingestion_job_id == dead_id
                await session.execute(
                    update(IngestionJob)
                    .where(IngestionJob.ingestion_job_id == dead_id)
                    .values(lease_expires_at=utcnow() - timedelta(seconds=1))
                )
                await session.commit()
                assert not await repo.heartbeat(dead_id)
                assert not await repo.finish(dead_id, output={"invalid": True})
                assert await repo.recover_expired() == 1
                assert await repo.claim() is None
            dead = (await api.get(f"/ingestion/jobs/{dead_id}")).json()
            assert dead["error_message"] == "WORKER_LEASE_EXPIRED"
            history = (await api.get("/ingestion/jobs", params={"data_source_id": 1})).json()
            assert len(history) == 5
            assert all("configuration" not in job for job in history)
            async with factory() as session:
                assert await session.scalar(select(DataSource.ingestion_count)) == 1
            path.write_bytes(b"id,name\n002,Another\n")

            def fail_store(*args):
                raise RuntimeError("secret-storage-credential")

            monkeypatch.setattr(ingestion_worker, "store_bronze", fail_store)
            write_id = (await api.post("/ingestion/jobs", json=payload)).json()["ingestion_job_id"]
            assert await ingestion_worker.run_once()
            write_failed = (await api.get(f"/ingestion/jobs/{write_id}")).json()
            assert write_failed["error_message"] == "INGESTION_WRITING_FAILED"
            assert write_failed["rows_read"] == 1
            assert write_failed["rows_written"] == 0
            async with factory() as session:
                await session.execute(
                    update(DataSource).where(DataSource.data_source_id == 1).values(is_active=False)
                )
                await session.commit()
            rejected = await api.post("/ingestion/jobs", json=payload)
            assert rejected.status_code == 422
            rejection_id = rejected.json()["error"]["details"]["ingestion_job_id"]
            assert (await api.get(f"/ingestion/jobs/{rejection_id}")).json()["status"] == "REJECTED"
    finally:
        await engine.dispose()
        await conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        await conn.close()
        # Chỉ admin fixture dọn dữ liệu test trong prefix UUID này, không xóa bucket.
        for obj in storage.list_objects(
            bucket, prefix=partition + "/", recursive=True, include_version=True
        ):
            assert obj.object_name is not None
            storage.remove_object(bucket, obj.object_name, version_id=obj.version_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(("source_type", "domain"), [("EXCEL", "academic"), ("POSTGRESQL", "hr")])
async def test_validated_excel_and_postgresql_are_written_to_bronze(
    tmp_path, monkeypatch, source_type, domain
):
    """Chạy connector thật với PostgreSQL/MinIO và xác minh bytes/schema Bronze."""
    suffix = uuid4().hex
    schema, bucket = "ingestion_test_" + suffix, settings.minio_bucket_bronze
    source_schema = "source_" + suffix
    partition = f"{domain}-validation-{suffix}"
    root = Path(__file__).resolve().parents[1]
    ddl = (root / "infra/postgres/init/02-schema.sql").read_text(encoding="utf-8")
    ddl = ddl.replace("CREATE SCHEMA IF NOT EXISTS app;", f"CREATE SCHEMA {schema};")
    ddl = ddl.replace("SET search_path TO app, public;", f"SET search_path TO {schema}, public;")
    storage = client()
    engine = create_async_engine(
        settings.database_url,
        poolclass=NullPool,
        execution_options={"schema_translate_map": {"app": schema}},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(ingestion_worker, "async_session_factory", factory)
    conn = await asyncpg.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        database=settings.db_name,
    )
    try:
        await conn.execute(ddl)
        await conn.execute(
            "INSERT INTO source_system(source_code, source_name, source_type) VALUES ($1, 'Test', $2)",
            source_type,
            source_type,
        )
        await conn.execute(
            "INSERT INTO data_layer(layer_code, layer_name) VALUES ('BRONZE', 'Bronze')"
        )
        await conn.execute(
            """INSERT INTO dataset(dataset_code,dataset_name,domain,source_system_id,data_layer_id)
               VALUES ($1, 'Test', $2, 1, 1)""",
            f"dataset-{suffix}",
            partition,
        )
        if source_type == "EXCEL":
            path = tmp_path / "academic.xlsx"
            workbook = Workbook()
            assert workbook.active is not None
            worksheet = workbook.active
            worksheet.title = "Academic"
            worksheet.append(["student_id", "course_name"])
            worksheet.append(["S001", "Data Engineering"])
            workbook.save(path)
            workbook.close()
            location, source_table = str(path), path.name
            configuration = {"sheet_name": "Academic", "header_row": 1, "skip_rows": 0}
            policy = _validation_policy(
                source_type="EXCEL",
                parser={
                    "sheet_name": "Academic",
                    "header_row": 1,
                    "skip_rows": 0,
                    "formula_policy": "reject",
                },
                columns=[
                    {"name": "student_id", "required": True},
                    {"name": "course_name", "required": True},
                ],
            )
        else:
            await conn.execute(f"CREATE SCHEMA {source_schema}")
            await conn.execute(
                f"CREATE TABLE {source_schema}.employee (employee_id INTEGER, full_name TEXT, salary NUMERIC(10,2))"
            )
            await conn.execute(
                f"INSERT INTO {source_schema}.employee VALUES (1, 'Nguyen Van A', 12345.67)"
            )
            location, source_table = None, f"{source_schema}.employee"
            configuration = {
                "host": settings.db_host,
                "port": settings.db_port,
                "database": settings.db_name,
                "username": settings.db_user,
                "password": settings.db_password,
                "connection_config_version": "live-v1",
            }
            policy = _validation_policy(
                source_type="POSTGRESQL",
                parser={
                    "schema_name": source_schema,
                    "table_name": "employee",
                    "connection_config_version": "live-v1",
                },
                columns=[
                    {"name": "employee_id", "allowed_source_types": ["int4"]},
                    {"name": "full_name", "allowed_source_types": ["text"]},
                    {"name": "salary", "allowed_source_types": ["numeric"]},
                ],
            )
        await conn.execute(
            """INSERT INTO data_source(
                    source_system_id,source_name,source_type,location,configuration,updated_at
                ) VALUES (1, $1, $2, $3, $4::jsonb, NOW())""",
            source_type,
            source_type,
            location,
            json.dumps(configuration),
        )
        user = {"sub": "test-user", "roles": ["ADMIN"]}
        app = _test_app(factory, user)
        payload = {
            "data_source_id": 1,
            "dataset_id": 1,
            "source_table": source_table,
            "validation_policy": policy,
        }
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as api:
            queued = await api.post("/ingestion/jobs", json=payload)
            assert queued.status_code == 202, queued.text
            job_id = queued.json()["ingestion_job_id"]
            assert await ingestion_worker.run_once()
            result = (await api.get(f"/ingestion/jobs/{job_id}")).json()
        assert result["status"] == "SUCCESS", result
        assert result["validation_report"]["result"] == "PASS"
        assert result["validation_report"]["records_scanned"] == 1
        assert result["output"]["version_id"]
        key = result["output"]["uri"].split(f"s3://{bucket}/", 1)[1]
        response = bronze_reader_client().get_object(
            bucket, key, version_id=result["output"]["version_id"]
        )
        try:
            raw = response.read()
        finally:
            response.close()
            response.release_conn()
        if source_type == "EXCEL":
            assert location is not None
            assert raw == Path(location).read_bytes()
        else:
            table = pq.read_table(io.BytesIO(raw))
            assert table.column_names == ["employee_id", "full_name", "salary"]
            assert table.schema.field("employee_id").type == pa.int32()
            assert table.schema.field("salary").type == pa.decimal128(10, 2)
            assert table.to_pylist()[0]["full_name"] == "Nguyen Van A"
    finally:
        await engine.dispose()
        await conn.execute(f"DROP SCHEMA IF EXISTS {source_schema} CASCADE")
        await conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        await conn.close()
        for obj in storage.list_objects(
            bucket, prefix=partition + "/", recursive=True, include_version=True
        ):
            assert obj.object_name is not None
            storage.remove_object(bucket, obj.object_name, version_id=obj.version_id)
