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
from app.modules.pipeline.repositories.silver import SilverRepository, object_name
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
                text("""INSERT INTO app.cleaning_run(run_id,ingestion_job_id,dataset_id,domain,entity,requested_by,
                session,bronze_version,cleaning_status,publication_status,records_published,silver_snapshot_id,started_at,finished_at)
                VALUES(:id,:upload,31,'hr','employee','1','production','bronze-test','COMPLETED','PUBLISHED',2,:id,now(),now())"""),
                args,
            )
            conn.execute(
                text("SELECT app.enqueue_gold_object(:scope,:id,CAST(:ref AS jsonb))"), args
            )
            if rollback:
                tx.rollback()
            else:
                tx.commit()
        if not rollback:
            repository = SilverRepository(engine)
            state = repository._state(scope)
            state["head"] = args["id"]
            state["snapshots"][args["id"]] = {
                "snapshot_id": args["id"],
                "scope": scope,
                "request_hash": args["hash"],
                "manifest_ref": args["ref"],
                "status": "PUBLISHED",
            }
            repository._write(state)
            repository.store.write(object_name("versions", args["id"]), {"scope": scope})
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
        first = conn.execute(
            text(
                "SELECT app.enqueue_gold_object(:scope,:silver_snapshot_id,CAST(:silver_manifest_ref AS jsonb))"
            ),
            source,
        ).scalar_one()
        assert (
            conn.execute(
                text(
                    "SELECT app.enqueue_gold_object(:scope,:silver_snapshot_id,CAST(:silver_manifest_ref AS jsonb))"
                ),
                source,
            ).scalar_one()
            == first
        )
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


def test_manager_reads_current_gold_with_dataset_ownership_and_pagination(published):
    from sqlalchemy.orm import Session

    from app.dependencies import get_db
    from tests.test_staff_workflow_postgres import asynchronous

    engine, publish = published
    with engine.begin() as conn:
        department = conn.execute(
            text("SELECT department_id FROM app.department WHERE department_code='PNS'")
        ).scalar_one()
        other = conn.execute(
            text("SELECT department_id FROM app.department WHERE department_code='PDT'")
        ).scalar_one()
        conn.execute(
            text("UPDATE app.dataset SET owner_department_id=:department WHERE dataset_id=31"),
            {"department": department},
        )
        conn.execute(
            text(
                "INSERT INTO app.dataset(dataset_id,dataset_code,dataset_name,domain,data_layer_id,owner_department_id) "
                "OVERRIDING SYSTEM VALUE VALUES (32,'other_hr','Other HR','hr',1,:other),"
                "(33,'unowned','Unowned HR','hr',1,NULL),(34,'academic','Academic','academic',1,:department)"
            ),
            {"department": department, "other": other},
        )
        employee = conn.execute(
            text(
                "INSERT INTO app.employee(employee_code,full_name,employee_status) VALUES('NV9999','Manager Test','Active') RETURNING employee_id"
            )
        ).scalar_one()
        user_id = conn.execute(
            text(
                "INSERT INTO app.app_user(username,password_hash,employee_id) VALUES('manager_test','unused',:employee) RETURNING app_user_id"
            ),
            {"employee": employee},
        ).scalar_one()
        role_id = conn.execute(
            text(
                "INSERT INTO app.role(role_code,role_name) VALUES('HR_MANAGER','HR manager') RETURNING role_id"
            )
        ).scalar_one()
        conn.execute(
            text("INSERT INTO app.user_role(app_user_id,role_id) VALUES(:user,:role)"),
            {"user": user_id, "role": role_id},
        )
        position = conn.execute(
            text(
                "INSERT INTO app.position(position_code,position_name) VALUES('MTEST','Manager Test') RETURNING position_id"
            )
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO app.assignment(employee_id,department_id,position_id,start_date,assignment_status) VALUES(:employee,:department,:position,current_date-2,'Active')"
            ),
            {"employee": employee, "department": department, "position": position},
        )
        visible_issue, hidden_issue = str(uuid4()), str(uuid4())
        conn.execute(
            text(
                "INSERT INTO app.data_quality_issue(issue_id,dataset_id,department_id,domain,title,description,status,created_by,created_at,updated_at) "
                "VALUES(:id,31,:department,'hr','Cần sửa','Test','ASSIGNED',:user,now(),now())"
            ),
            [
                {"id": visible_issue, "department": department, "user": user_id},
                {"id": hidden_issue, "department": other, "user": user_id},
            ],
        )
        conn.execute(
            text(
                "INSERT INTO app.data_quality_issue_event(issue_id,actor_id,event_type,to_status,details,created_at) "
                "VALUES(:id,:user,'ASSIGNED','ASSIGNED','{}',now())"
            ),
            {"id": visible_issue, "user": user_id},
        )

    async def db_override():
        with Session(engine) as session:
            yield asynchronous(session)
            session.commit()

    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[gold_engine] = lambda: engine
    app.dependency_overrides[get_current_user] = lambda: {
        "sub": str(user_id),
        "roles": ["HR_MANAGER"],
    }
    try:
        client = TestClient(app)
        initial = client.get("/api/v1/manager/overview").json()
        assert initial["status"] == "NO_DATA"
        assert initial["datasets"] == 1
        assert initial["kpis"]["items"] == []
        assert initial["issues"]["total"] == 1
        assert len(client.get("/api/v1/manager/issues").json()) == 1
        assert client.get("/api/v1/manager/issues/stats").json()["assigned"] == 1
        detail = client.get(f"/api/v1/manager/issues/{visible_issue}")
        assert detail.status_code == 200, detail.text
        assert len(detail.json()["history"]) == 1
        assert client.get(f"/api/v1/manager/issues/{hidden_issue}").status_code == 404
        assert client.get(f"/api/v1/manager/issues?department_id={other}").json() == []
        notifications = client.get("/api/v1/notifications?workspace=MANAGER")
        assert notifications.status_code == 200, notifications.text
        feed = notifications.json()
        assert feed["total"] == feed["unread_count"] == 1
        event_id = feed["items"][0]["event_id"]
        assert (
            client.post("/api/v1/notifications/read", json={"event_ids": [event_id]}).status_code
            == 204
        )
        assert client.get("/api/v1/notifications").json()["unread_count"] == 0
        assert client.get("/api/v1/notifications").json()["items"][0]["read_at"]
        source, silver, gold = publish()
        response = client.get("/api/v1/manager/overview")
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "PENDING"
        repository = GoldRepository(engine)
        claimed = repository.claim("manager-test")
        assert claimed is not None
        assert build_gold(claimed, silver, gold, repository)
        response = client.get("/api/v1/manager/overview")
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "READY"
        assert response.json()["kpis"]["items"][0]["value"] == 2
        assert response.json()["last_updated_at"]
        legacy = client.get("/api/v1/pipeline/gold/metrics")
        assert legacy.status_code == 200, legacy.text
        assert len(legacy.json()) == 2
        legacy_job = client.get("/api/v1/pipeline/gold/jobs").json()[0]["job_id"]
        assert client.get(f"/api/v1/pipeline/gold/jobs/{legacy_job}/report").status_code == 200
        # Không thể đi vòng qua API Gold cũ để đọc nguồn thuộc phòng khác.
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE app.dataset SET owner_department_id=:other WHERE dataset_id=31"),
                {"other": other},
            )
        assert client.get("/api/v1/pipeline/gold/metrics").json() == []
        assert client.get(f"/api/v1/pipeline/gold/jobs/{legacy_job}/report").status_code == 404
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE app.dataset SET owner_department_id=:department WHERE dataset_id=31"),
                {"department": department},
            )
        assert client.get("/api/v1/manager/datasets").json()["total"] == 1
        assert (
            client.get("/api/v1/manager/datasets/31").json()["sources"][0]["sync_status"]
            == "CURRENT"
        )
        for hidden in (32, 33, 34, 999):
            assert client.get(f"/api/v1/manager/datasets/{hidden}").status_code == 404
            assert (
                client.get(f"/api/v1/manager/analytics?dataset_id={hidden}").json()["items"] == []
            )
        grouped = client.get(
            "/api/v1/manager/analytics?entity=employee&metric=record_count_by_employee_status"
        ).json()
        assert grouped["items"][0]["dimensions"] == {"employee_status": "ACTIVE"}
        assert grouped["items"][0]["value"] == 2
        # Hai nguồn có cùng người vẫn trả hai KPI riêng, không tự cộng thành bốn người.
        _, silver2, gold2 = publish(scope="production:another-manager-source")
        claimed2 = repository.claim("manager-test")
        assert claimed2 is not None
        assert build_gold(claimed2, silver2, gold2, repository)
        page = client.get("/api/v1/manager/analytics?limit=1&offset=1").json()
        assert page["total"] == 2
        assert len(page["items"]) == 1
        assert page["items"][0]["value"] == 2
        assert client.get("/api/v1/manager/analytics?offset=10").json()["total"] == 2
        # Silver thay đổi: bản Gold trước đó không còn hiện qua manager API.
        publish(scope=source["scope"])
        stale = client.get("/api/v1/manager/overview").json()
        assert stale["status"] == "PARTIAL"
        assert stale["sources"]["queued"] == 1
        assert stale["kpis"]["total"] == 1
        assert stale["kpis"]["items"][0]["scope"] != source["scope"]
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE app.assignment SET end_date=current_date-1 WHERE employee_id=:employee"
                ),
                {"employee": employee},
            )
        assert not client.get("/api/v1/manager/context").json()["needs_department_assignment"]
        assert client.get("/api/v1/manager/overview").status_code == 200
        assert len(client.get("/api/v1/notifications").json()["items"]) == 1
        assert (
            client.post("/api/v1/notifications/read", json={"event_ids": [event_id]}).status_code
            == 204
        )
        assert len(client.get("/api/v1/pipeline/gold/metrics").json()) == 2
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(gold_engine, None)


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
            text(
                "SELECT app.enqueue_gold_object(:scope,:silver_snapshot_id,CAST(:silver_manifest_ref AS jsonb))"
            ),
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


def test_database_sync_retry_requires_admin_and_current_blocked_gold(published):
    engine, publish = published
    source, silver, gold = publish()
    repository = GoldRepository(engine)
    job = repository.claim("retry-business-test")
    assert job is not None
    assert build_gold(job, silver, gold, repository)
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE app.gold_sync_job SET status='BLOCKED',error_code='GOLD_SYNC_REQUIRED_FIELD',error_report='{}'::jsonb WHERE gold_job_id=:job_id"
            ),
            job,
        )
    app.dependency_overrides[gold_engine] = lambda: engine
    user = {"sub": "1", "roles": ["HR_STAFF"]}
    app.dependency_overrides[get_current_user] = lambda: user
    path = f"/api/v1/pipeline/gold/jobs/{job['job_id']}/database-sync/retry"
    with TestClient(app) as client:
        assert client.post(path).status_code == 403
        user["roles"] = ["DATA_ADMIN"]
        assert client.post(path).status_code == 202
        assert client.post(path).status_code == 409
        with engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT status,error_code,error_report FROM app.gold_sync_job WHERE gold_job_id=:job_id"
                ),
                job,
            ).one()
            assert row == ("QUEUED", None, None)
            conn.execute(
                text("UPDATE app.gold_sync_job SET status='BLOCKED' WHERE gold_job_id=:job_id"), job
            )
        publish(scope=source["scope"])
        assert client.post(path).status_code == 409
