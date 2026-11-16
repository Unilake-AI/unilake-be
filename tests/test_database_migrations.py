"""Kiểm thử entrypoint migration thật bằng psql trên database PostgreSQL cô lập."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from app.core.config import settings

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "infra/postgres/migrations"
LIVE = pytest.mark.skipif(
    os.getenv("MIGRATION_LIVE_TEST") != "1", reason="Cần PostgreSQL Docker local"
)


def required_row(row: tuple[Any, ...] | None) -> tuple[Any, ...]:
    assert row is not None
    return row


def test_upgrade_includes_every_migration_in_dependency_order():
    included = re.findall(r"^\\ir (\S+\.sql)$", (MIGRATIONS / "upgrade.psql").read_text(), re.M)
    assert included == [p.name for p in sorted(MIGRATIONS.glob("*.sql"))]
    assert included.index("20261015_p5_silver.sql") < included.index("20261116_gold_pipeline.sql")
    assert included.index("20261115_auto_silver_pipeline.sql") < included.index(
        "20261116_silver_default_automation.sql"
    )
    assert included[-1] == "20261116_storage_model_v2.sql"


@pytest.fixture
def isolated_database() -> Iterator[str]:
    assert settings.db_host in {"localhost", "127.0.0.1", "::1"}
    name = "unilake_migration_test_" + uuid4().hex
    with connect(settings.db_name) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            yield name
        finally:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def connect(name: str):
    return psycopg.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        dbname=name,
        autocommit=True,
    )


def psql(name: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "postgres",
            "psql",
            "-X",
            "-U",
            settings.db_user,
            "-d",
            name,
            "-v",
            "ON_ERROR_STOP=1",
            *args,
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )


def upgrade(name: str) -> None:
    result = psql(name, "-f", "/opt/migrations/upgrade.psql")
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]


@LIVE
def test_fresh_database_has_complete_schema_and_can_be_upgraded_twice(isolated_database):
    name = isolated_database
    upgrade(name)
    with connect(name) as db:
        history = db.execute(
            "SELECT migration_name,applied_at FROM app.schema_migration ORDER BY migration_name"
        ).fetchall()
        assert {row[0] for row in history} == {p.name for p in MIGRATIONS.glob("*.sql")}
        assert {r[0] for r in db.execute("SELECT layer_code FROM app.data_layer")} == {
            "BRONZE",
            "SILVER",
            "GOLD",
        }
        for table in (
            "app.gold_sync_job",
            "app.gold_record_identity",
            "app.identity_crosswalk",
            "app.gold_job",
            "gold.aggregate",
            "gold.current_metrics",
            "app.notification_read",
        ):
            assert (
                required_row(db.execute("SELECT to_regclass(%s)", (table,)).fetchone())[0]
                is not None
            )
        # Migration chỉ tạo schema/reference, không tạo hồ sơ hoặc cấp quyền mẫu.
        for table in ("app.employee", "app.student", "app.dataset", "app.app_user"):
            assert (
                required_row(
                    db.execute(
                        sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(*table.split(".")))
                    ).fetchone()
                )[0]
                == 0
            )
    upgrade(name)
    with connect(name) as db:
        assert (
            db.execute(
                "SELECT migration_name,applied_at FROM app.schema_migration ORDER BY migration_name"
            ).fetchall()
            == history
        )
        assert required_row(db.execute("SELECT count(*) FROM app.data_layer").fetchone())[0] == 3


@LIVE
def test_repairs_exact_partial_init_without_overwriting_existing_data(isolated_database):
    name = isolated_database
    base = psql(name, "-f", "/docker-entrypoint-initdb.d/02-schema.sql")
    assert base.returncode == 0, base.stderr
    auto = psql(name, "-f", "/opt/migrations/20261115_auto_silver_pipeline.sql")
    assert auto.returncode == 0, auto.stderr
    # Tái hiện lỗi cũ: Gold được gọi khi chưa có nền Silver.
    broken = psql(name, "-f", "/opt/migrations/20261116_gold_pipeline.sql")
    assert broken.returncode != 0
    assert "app.silver_head" in broken.stderr
    with connect(name) as db:
        db.execute(
            "INSERT INTO app.data_layer(layer_code,layer_name,storage_type) VALUES ('BRONZE','Tên quản trị đã sửa','CUSTOM')"
        )
        layer = required_row(
            db.execute(
                "SELECT data_layer_id,layer_name,storage_type FROM app.data_layer WHERE layer_code='BRONZE'"
            ).fetchone()
        )
        employee_id = required_row(
            db.execute(
                "INSERT INTO app.employee(employee_code,full_name,employee_status) VALUES ('REAL-001','Nhân viên kiểm thử migration','ACTIVE') RETURNING employee_id"
            ).fetchone()
        )[0]
        db.execute(
            "INSERT INTO app.app_user(username,password_hash,employee_id) VALUES ('existing-admin','existing-password-hash',%s)",
            (employee_id,),
        )
    upgrade(name)
    upgrade(name)
    with connect(name) as db:
        assert (
            required_row(
                db.execute(
                    "SELECT data_layer_id,layer_name,storage_type FROM app.data_layer WHERE layer_code='BRONZE'"
                ).fetchone()
            )
            == layer
        )
        assert required_row(
            db.execute(
                "SELECT password_hash,employee_id FROM app.app_user WHERE username='existing-admin'"
            ).fetchone()
        ) == ("existing-password-hash", employee_id)
        assert (
            required_row(db.execute("SELECT to_regclass('app.gold_job')").fetchone())[0]
            == "app.gold_job"
        )
        assert (
            required_row(
                db.execute(
                    "SELECT to_regprocedure('app.enqueue_default_silver(bigint)')"
                ).fetchone()
            )[0]
            is not None
        )


@LIVE
def test_existing_older_core_gets_columns_before_current_indexes(isolated_database):
    name = isolated_database
    result = psql(name, "-f", "/docker-entrypoint-initdb.d/02-schema.sql")
    assert result.returncode == 0, result.stderr
    with connect(name) as db:
        # Chỉ xóa cột trong DB kiểm thử rỗng để mô phỏng schema trước Reliability MVP.
        db.execute("ALTER TABLE app.ingestion_job DROP COLUMN root_job_id CASCADE")
    upgrade(name)
    with connect(name) as db:
        assert (
            required_row(
                db.execute(
                    "SELECT count(*) FROM information_schema.columns WHERE table_schema='app' AND table_name='ingestion_job' AND column_name='root_job_id'"
                ).fetchone()
            )[0]
            == 1
        )
        assert (
            required_row(
                db.execute("SELECT to_regclass('app.ix_ingestion_job_root_attempt')").fetchone()
            )[0]
            is not None
        )


@LIVE
def test_nonempty_silver_requires_verified_export_and_rejects_stale_proof(isolated_database):
    """DROP bị chặn khi chưa export hoặc có dòng mới sau lần export."""
    from sqlalchemy import URL, create_engine

    from app.infra.minio.bronze import client as admin_storage
    from app.modules.pipeline.repositories.object_metadata import ObjectMetadata
    from app.modules.pipeline.repositories.silver import SilverRepository
    from app.modules.pipeline.services.migrate_silver_metadata import export_metadata

    name = isolated_database
    upgrade(name)
    for filename in (
        "20261015_p5_silver.sql",
        "20261110_silver_management.sql",
        "20261112_silver_profile_governance.sql",
        "20261115_auto_silver_pipeline.sql",
    ):
        result = psql(name, "-f", "/opt/migrations/" + filename)
        assert result.returncode == 0, result.stderr
    identifier = str(uuid4())
    with connect(name) as db:
        db.execute(
            "INSERT INTO app.silver_run(run_id,scope,request_hash,status) VALUES(%s,'production:migration','hash','PUBLISHED')",
            (identifier,),
        )
        db.execute(
            "INSERT INTO app.silver_snapshot(snapshot_id,scope,request_hash,manifest_ref) VALUES(%s,'production:migration','hash','{}')",
            (identifier,),
        )
        db.execute(
            "INSERT INTO app.silver_head(scope,snapshot_id) VALUES('production:migration',%s)",
            (identifier,),
        )
    result = psql(name, "-f", "/opt/migrations/20261116_storage_model_v2.sql")
    assert result.returncode != 0
    assert "SILVER_METADATA_EXPORT_REQUIRED" in result.stderr
    engine = create_engine(
        URL.create(
            "postgresql+psycopg",
            username=settings.db_user,
            password=settings.db_password,
            host=settings.db_host,
            port=settings.db_port,
            database=name,
        )
    )
    store = ObjectMetadata(engine)
    try:
        assert export_metadata(engine)["silver_run"] == 1
        with connect(name) as db:
            db.execute(
                "INSERT INTO app.silver_run(run_id,scope,request_hash,status) VALUES(%s,'production:new','hash2','FAILED')",
                (str(uuid4()),),
            )
        result = psql(name, "-f", "/opt/migrations/20261116_storage_model_v2.sql")
        assert result.returncode != 0
        assert "SILVER_METADATA_EXPORT_STALE" in result.stderr
        assert export_metadata(engine)["silver_run"] == 2
        result = psql(name, "-f", "/opt/migrations/20261116_storage_model_v2.sql")
        assert result.returncode == 0, result.stderr
        assert (
            SilverRepository(engine).snapshot(identifier, "production:migration")["snapshot_id"]
            == identifier
        )
        with connect(name) as db:
            assert (
                required_row(
                    db.execute(
                        "SELECT count(*) FROM pg_tables WHERE schemaname='app' AND left(tablename,7)='silver_'"
                    ).fetchone()
                )[0]
                == 0
            )
    finally:
        storage = admin_storage()
        for item in storage.list_objects(
            settings.minio_bucket_silver, prefix=store.prefix, recursive=True, include_version=True
        ):
            if item.object_name:
                storage.remove_object(
                    settings.minio_bucket_silver, item.object_name, version_id=item.version_id
                )
        engine.dispose()
