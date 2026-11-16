"""Kiểm thử Gold trên database riêng và object riêng của lượt thử."""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.config import settings
from app.core.deps import get_current_user
from app.infra.minio.bronze import client as admin_storage
from app.main import app
from app.modules.pipeline.repositories.gold import GoldRepository
from app.modules.pipeline.repositories.silver_storage import ObjectRef, checksum
from app.modules.pipeline.routers.gold import gold_engine
from app.modules.pipeline.services import gold_worker, silver_worker
from app.modules.pipeline.services.gold_pipeline import build_gold
from tests.test_auto_pipeline_live import system as system
from tests.test_gold_pipeline import source_fixture

pytestmark = pytest.mark.skipif(
    os.getenv("GOLD_LIVE_TEST") != "1",
    reason="Cần bật kiểm thử Gold trên PostgreSQL và MinIO local",
)


@pytest.fixture
def published(system, tmp_path):
    engine = system["engine"]
    upload_id = system["upload"]()
    system["ingestion"]()

    def publish(*, scope="production:test", rollback=False):
        job, silver, gold = source_fixture(tmp_path / uuid4().hex, dataset_id=31)
        job["scope"], job["dataset_id"] = scope, 31
        original = json.loads(silver.get(ObjectRef(**json.loads(job["silver_manifest_ref"]))))
        original["scope"] = scope
        encoded = json.dumps(original).encode()
        job["silver_manifest_ref"] = json.dumps(
            asdict(
                silver.put(
                    "silver/production/fixture/manifest-" + checksum(encoded) + ".json", encoded
                )
            )
        )
        args = {
            "id": job["silver_snapshot_id"],
            "scope": scope,
            "hash": uuid4().hex,
            "ref": job["silver_manifest_ref"],
            "upload": upload_id,
        }
        with engine.connect() as conn:
            tx = conn.begin()
            conn.execute(
                text(
                    "INSERT INTO app.silver_run(run_id,scope,request_hash,status) VALUES(:id,:scope,:hash,'PUBLISHED')"
                ),
                args,
            )
            conn.execute(
                text(
                    "INSERT INTO app.silver_snapshot(snapshot_id,scope,request_hash,manifest_ref) VALUES(:id,:scope,:hash,:ref)"
                ),
                args,
            )
            conn.execute(
                text(
                    "INSERT INTO app.silver_head(scope,snapshot_id) VALUES(:scope,:id) ON CONFLICT(scope) DO UPDATE SET snapshot_id=EXCLUDED.snapshot_id"
                ),
                args,
            )
            conn.execute(
                text("""INSERT INTO app.cleaning_run(run_id,ingestion_job_id,dataset_id,domain,entity,requested_by,
                session,bronze_version,cleaning_status,publication_status,records_published,silver_snapshot_id,started_at,finished_at)
                VALUES(:id,:upload,31,'hr','employee','1','production','bronze-test','COMPLETED','PUBLISHED',2,:id,now(),now())"""),
                args,
            )
            if rollback:
                tx.rollback()
            else:
                tx.commit()
        return job, silver, gold

    yield engine, publish
    app.dependency_overrides.pop(gold_engine, None)
    app.dependency_overrides.pop(get_current_user, None)


def test_commit_enqueues_once_rollback_never_enqueues(published):
    engine, publish = published
    publish(rollback=True)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM app.gold_job")).scalar_one() == 0
    source, _, _ = publish()
    with engine.begin() as conn:
        first = conn.execute(text("SELECT app.enqueue_gold(:scope)"), source).scalar_one()
        assert conn.execute(text("SELECT app.enqueue_gold(:scope)"), source).scalar_one() == first
        assert conn.execute(text("SELECT count(*) FROM app.gold_job")).scalar_one() == 1


def test_gold_postgres_csv_and_domain_access(published):
    engine, publish = published
    source, silver, gold = publish()
    repository = GoldRepository(engine)
    job = repository.claim("gold-test")
    assert job is not None
    assert repository.claim("another-worker") is None
    assert build_gold(job, silver, gold, repository)
    with engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT value FROM gold.hr_summary WHERE metric='record_count'")
            ).scalar_one()
            == 2
        )
        assert conn.execute(text("SELECT status FROM app.gold_attempt")).scalar_one() == "SUCCEEDED"
        assert (
            conn.execute(
                text("SELECT count(*) FROM app.pipeline_lineage WHERE target_layer='GOLD'")
            ).scalar_one()
            == 1
        )
    app.dependency_overrides[gold_engine] = lambda: engine
    current = {"sub": "14", "roles": ["HR_STAFF"]}
    app.dependency_overrides[get_current_user] = lambda: current
    client = TestClient(app)
    assert len(client.get("/api/v1/pipeline/gold/metrics").json()) == 2
    assert client.get("/api/v1/pipeline/gold/jobs").status_code == 200
    response = client.get(f"/api/v1/pipeline/gold/jobs/{job['job_id']}/report")
    assert response.status_code == 200
    assert "record_count" in response.text
    assert len(client.get(f"/api/v1/pipeline/gold/jobs/{job['job_id']}/attempts").json()) == 1
    current["roles"] = ["ACADEMIC_STAFF"]
    assert client.get("/api/v1/pipeline/gold/metrics?domain=hr").json() == []
    assert client.get(f"/api/v1/pipeline/gold/jobs/{job['job_id']}").status_code == 404
    assert client.get(f"/api/v1/pipeline/gold/jobs/{job['job_id']}/report").status_code == 404
    current["roles"] = ["DATA_ADMIN"]
    response = client.post(
        "/api/v1/pipeline/gold/refresh", json={"silver_snapshot_id": source["silver_snapshot_id"]}
    )
    assert response.status_code == 202
    assert response.json()["job_id"] == job["job_id"]
    assert client.get("/api/v1/pipeline/gold/sources").json()[0]["sync_status"] == "CURRENT"


def test_new_silver_fences_stale_worker_and_rollback_hides_old_gold(published):
    engine, publish = published
    source, silver, gold = publish()
    repository = GoldRepository(engine)
    old = repository.claim("one")
    assert old is not None
    _, new_silver, new_gold = publish(scope=source["scope"])
    assert not build_gold(old, silver, gold, repository)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM gold.current_metrics")).scalar_one() == 0
        assert (
            conn.execute(
                text("SELECT status FROM app.gold_job WHERE job_id=:job_id"), old
            ).scalar_one()
            == "SUPERSEDED"
        )
    new = repository.claim("two")
    assert new is not None
    assert build_gold(new, new_silver, new_gold, repository)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE app.silver_head SET snapshot_id=:silver_snapshot_id WHERE scope=:scope"),
            source,
        )
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM gold.current_metrics")).scalar_one() == 0
        assert conn.execute(text("SELECT max(generation) FROM app.gold_job")).scalar_one() == 3
    back = repository.claim("three")
    assert back is not None
    assert build_gold(back, silver, gold, repository)


def test_expired_lease_keeps_attempt_history(published):
    engine, publish = published
    _, silver, gold = publish()
    repository = GoldRepository(engine)
    first = repository.claim("one")
    assert first is not None
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE app.gold_job SET lease_until=now()-interval '1 second' WHERE job_id=:job_id"
            ),
            first,
        )
    second = repository.claim("two")
    assert second is not None
    assert second["attempt_number"] == 2
    assert not repository.heartbeat("one", first)
    assert not build_gold(first, silver, gold, repository)
    assert build_gold(second, silver, gold, repository)
    with engine.connect() as conn:
        assert conn.execute(
            text("SELECT status FROM app.gold_attempt ORDER BY attempt_number")
        ).scalars().all() == ["FAILED", "SUCCEEDED"]


def test_manual_retry_keeps_attempts_and_refuses_running_job(published):
    engine, publish = published
    publish()
    repository = GoldRepository(engine)
    first = repository.claim("one")
    assert first is not None
    repository.fail(first, "GOLD_SOURCE_EVIDENCE_REQUIRED", False)
    app.dependency_overrides[gold_engine] = lambda: engine
    app.dependency_overrides[get_current_user] = lambda: {"sub": "1", "roles": ["DATA_ADMIN"]}
    client = TestClient(app)
    assert client.post(f"/api/v1/pipeline/gold/jobs/{first['job_id']}/retry").status_code == 202
    again = repository.claim("two")
    assert again is not None
    assert again["attempt_number"] == 2
    assert client.post(f"/api/v1/pipeline/gold/jobs/{first['job_id']}/retry").status_code == 409


def test_actual_upload_bronze_silver_gold_with_scoped_minio(system):
    system["policy"]()
    system["upload"]()
    system["ingestion"]()
    assert silver_worker.run_once(system["engine"], "silver-gold-live")
    with system["engine"].connect() as conn:
        job = dict(conn.execute(text("SELECT * FROM app.gold_job")).mappings().one())
    try:
        assert gold_worker.run_once(system["engine"], "gold-live")
        with system["engine"].connect() as conn:
            row = conn.execute(text("SELECT * FROM app.gold_job")).mappings().one()
            assert row["status"] == "SUCCEEDED", row["error_code"]
            assert row["result"]["records_read"] == 1
            assert (
                conn.execute(
                    text("SELECT sum(value) FROM gold.current_metrics WHERE metric='record_count'")
                ).scalar_one()
                == 1
            )
            assert row["result"]["silver_snapshot_id"] == job["silver_snapshot_id"]
    finally:
        # Chỉ dọn tệp Gold của chính job kiểm thử, không đụng các bản Gold người dùng.
        storage = admin_storage()
        prefix = "gold/production/" + checksum(job["scope"].encode()) + "/" + job["job_id"] + "/"
        for item in storage.list_objects(
            settings.minio_bucket_gold, prefix=prefix, recursive=True, include_version=True
        ):
            assert item.object_name is not None
            storage.remove_object(
                settings.minio_bucket_gold, item.object_name, version_id=item.version_id
            )
