"""Real uploads, Gold transactions, correction API, audit and concurrent writes."""

from __future__ import annotations

import asyncio
import csv
import json
import os
from concurrent.futures import ThreadPoolExecutor
from io import StringIO

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.dependencies import get_db
from app.modules.admissions.router import router
from app.modules.pipeline.services import gold_business_sync, gold_worker, silver_worker
from tests.test_auto_pipeline_live import system as system
from tests.test_default_silver_live import automatic as automatic
from tests.test_default_silver_live import process

pytestmark = pytest.mark.skipif(
    os.getenv("AUTO_PIPELINE_LIVE_TEST") != "1", reason="Local PostgreSQL and MinIO required"
)


@pytest.fixture
def workflow(automatic):
    system = automatic
    system["app"].include_router(router)
    accounts = {}
    with system["engine"].begin() as conn:
        conn.execute(
            text(
                "UPDATE app.dataset SET domain='admissions',owner_department_id=(SELECT department_id FROM app.department WHERE department_code='PTS') WHERE dataset_id=31"
            )
        )
        conn.execute(
            text(
                "INSERT INTO app.major(major_code,major_name,department_id) VALUES('CNTT','CNTT',(SELECT department_id FROM app.department WHERE department_code='PDT'))"
            )
        )
        conn.execute(
            text(
                "INSERT INTO app.program(program_code,program_name,major_id,degree_level) SELECT code,code,major_id,'BACHELOR' FROM app.major CROSS JOIN (VALUES ('CNTT'),('QTKD')) p(code)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO app.admission_year(year_label,start_date,end_date,status) VALUES('2026','2026-01-01','2026-12-31','OPEN')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO app.admission_method(method_code,method_name,max_score) VALUES('THPT','THPT',30),('XTT','Xét tuyển thẳng',0)"
            )
        )
        for role in (
            "ADMISSIONS_STAFF",
            "ADMISSIONS_MANAGER",
            "HR_MANAGER",
            "ACADEMIC_STAFF",
            "ADMIN",
            "DATA_ADMIN",
        ):
            role_id = conn.execute(
                text(
                    "INSERT INTO app.role(role_code,role_name) VALUES(:role,:role) RETURNING role_id"
                ),
                {"role": role},
            ).scalar_one()
            user_id = conn.execute(
                text(
                    "INSERT INTO app.app_user(username,password_hash) VALUES(:name,'unused') RETURNING app_user_id"
                ),
                {"name": role.lower()},
            ).scalar_one()
            conn.execute(
                text("INSERT INTO app.user_role VALUES(:user,:role)"),
                {"user": user_id, "role": role_id},
            )
            accounts[role] = user_id
    async_engine = create_async_engine(
        system["engine"].url.set(drivername="postgresql+asyncpg"), poolclass=NullPool
    )
    factory = async_sessionmaker(async_engine, expire_on_commit=False)

    async def db_override():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    system["app"].dependency_overrides[get_db] = db_override

    def as_role(role):
        system["user"].update(sub=str(accounts[role]), roles=[role])

    system.update(accounts=accounts, as_role=as_role)
    as_role("ADMISSIONS_STAFF")
    yield system
    asyncio.run(async_engine.dispose())


def request(system, method, path, **kwargs):
    return asyncio.run(system["request"](method, "/admissions" + path, **kwargs))


def upload(system, content, kind, *, filename=None):
    response = asyncio.run(
        system["request"](
            "POST",
            "/ingestion/uploads",
            data={"dataset_id": "31", "import_options": json.dumps({"import_type": kind})},
            files={"file": (filename or kind + ".csv", content.encode())},
        )
    )
    assert response.status_code == 202, response.text
    system["ingestion"]()
    job_id = response.json()["ingestion_job_id"]
    _profile, job = process(system, job_id)
    assert job["status"] == "SUCCESS", job
    assert gold_worker.run_once(system["engine"], "admission-workflow-test")
    assert gold_business_sync.run_once(system["engine"])
    with system["engine"].connect() as conn:
        row = (
            conn.execute(
                text(
                    "SELECT q.* FROM app.gold_sync_job q JOIN app.gold_job j ON q.gold_job_id=j.job_id WHERE j.silver_snapshot_id=:id"
                ),
                {"id": job["result"]["publication"]["snapshot_id"]},
            )
            .mappings()
            .one()
        )
    return dict(row)


def register(system, *, two=False, method="THPT"):
    content = (
        "applicant_code,full_name,program_code,year_label,method_code,preference_rank,admission_status\nTS001,Nguyễn An,CNTT,2026,"
        + method
        + ",1,APPLIED\n"
    )
    if two:
        content += "TS001,Nguyễn An,QTKD,2026," + method + ",2,APPLIED\n"
    result = upload(system, content, "admissions_registration")
    assert result["status"] == "SYNCED", result
    return rows(system)


def rows(system):
    response = request(system, "GET", "/registrations?dataset_id=31")
    assert response.status_code == 200, response.text
    return response.json()["items"]


def result_file(status="ACCEPTED", score="25", *, revision=1, program="CNTT"):
    return f"Mã thí sinh,Mã chương trình,Năm tuyển sinh,Mã phương thức,Phiên bản hiện tại,Điểm xét tuyển,Kết quả xét tuyển\nTS001,{program},2026,THPT,{revision},{score},{status}\n"


def correction(system, target, *, operation="RESULT_CORRECTION", changes=None, expected=None):
    response = request(
        system,
        "POST",
        "/corrections",
        json={
            "dataset_id": 31,
            "admission_id": target["admission_id"],
            "expected_revision": expected if expected is not None else target["revision"],
            "operation": operation,
            "reason": "Đối chiếu biên bản phòng tuyển sinh",
            "changes": changes or {"admission_score": "26.50"},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def apply(system, item):
    return request(system, "POST", f"/corrections/{item['correction_id']}/apply")


def test_result_file_replay_and_confirmation_record_audit_without_creating_student(workflow):
    system = workflow
    initial = register(system)
    assert initial[0]["revision"] == 1
    content = result_file()
    for index in range(2):
        synced = upload(system, content, "admissions_results")
        assert synced["status"] == "SYNCED", synced
        assert (
            synced["result"]["tables"]["admission"]["updated" if index == 0 else "unchanged"] == 1
        )
    current = rows(system)[0]
    assert (current["admission_status"], current["enrollment_status"], current["revision"]) == (
        "ACCEPTED",
        "PENDING",
        2,
    )
    enrollment = "Mã thí sinh,Mã chương trình,Năm tuyển sinh,Mã phương thức,Phiên bản hiện tại,Trạng thái nhập học,Ngày xác nhận nhập học\nTS001,CNTT,2026,THPT,2,CONFIRMED,2026-11-18\n"
    for _ in range(2):
        synced = upload(system, enrollment, "admissions_enrollment")
        assert synced["status"] == "SYNCED", synced
    current = rows(system)[0]
    assert current["revision"] == 3
    assert current["enrollment_status"] == "CONFIRMED"
    with system["engine"].connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM app.student")).scalar_one() == 0
        events = conn.execute(
            text(
                "SELECT operation,actor_id,old_revision,new_revision FROM app.admission_event ORDER BY new_revision"
            )
        ).all()
        assert [r[2:] for r in events] == [(None, 1), (1, 2), (2, 3)]
        assert {r[1] for r in events} == {system["accounts"]["ADMISSIONS_STAFF"]}
    exported = request(system, "GET", "/registrations/export?dataset_id=31&workflow=results")
    assert exported.status_code == 200, exported.text
    data = list(csv.DictReader(StringIO(exported.content.decode("utf-8-sig"))))
    assert data[0]["expected_revision"] == "3"
    history = request(
        system, "GET", f"/registrations/{current['admission_id']}/history?dataset_id=31"
    )
    assert history.status_code == 200
    assert history.json()["total"] == 3


def test_stale_file_is_blocked_and_whole_multirow_file_rolls_back(workflow):
    system = workflow
    register(system, two=True)
    first = upload(system, result_file("WAITLISTED", "20"), "admissions_results")
    assert first["status"] == "SYNCED", first
    stale = upload(
        system,
        result_file("ACCEPTED", "25", revision=1),
        "admissions_results",
        filename="stale.csv",
    )
    assert stale["status"] == "BLOCKED", stale
    assert stale["error_code"] == "ADMISSION_REVISION_CONFLICT"
    content = "applicant_code,program_code,year_label,method_code,expected_revision,admission_score,admission_status\nTS001,CNTT,2026,THPT,2,25,ACCEPTED\nTS001,QTKD,2026,THPT,1,31,ACCEPTED\n"
    blocked = upload(system, content, "admissions_results", filename="invalid.csv")
    assert blocked["status"] == "BLOCKED", blocked
    assert blocked["error_code"] == "ADMISSION_SCORE_INVALID"
    states = {r["program_code"]: (r["admission_status"], r["revision"]) for r in rows(system)}
    assert states == {"CNTT": ("WAITLISTED", 2), "QTKD": ("APPLIED", 1)}


def test_manager_preview_apply_history_and_concurrent_revision_guard(workflow):
    system = workflow
    register(system)
    assert upload(system, result_file(), "admissions_results")["status"] == "SYNCED"
    current = rows(system)[0]
    system["as_role"]("ADMISSIONS_MANAGER")
    pending = [
        correction(system, current, changes={"admission_score": score})
        for score in ("26.50", "27.00")
    ]
    assert rows(system)[0]["revision"] == 2
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda item: apply(system, item), pending))
    assert sorted(r.status_code for r in responses) == [200, 409], [r.text for r in responses]
    current = rows(system)[0]
    assert current["revision"] == 3
    winner = pending[next(i for i, r in enumerate(responses) if r.status_code == 200)]
    assert apply(system, winner).status_code == 200
    assert rows(system)[0]["revision"] == 3
    with system["engine"].connect() as conn:
        event = conn.execute(
            text(
                "SELECT actor_id,reason,correction_id,old_revision,new_revision FROM app.admission_event WHERE new_revision=3"
            )
        ).one()
        assert event == (
            system["accounts"]["ADMISSIONS_MANAGER"],
            winner["reason"],
            winner["correction_id"],
            2,
            3,
        )


@pytest.mark.parametrize("role", ["ADMISSIONS_STAFF", "HR_MANAGER", "ACADEMIC_STAFF", "DATA_ADMIN"])
def test_only_admissions_manager_or_admin_can_prepare_corrections(workflow, role):
    system = workflow
    current = register(system)[0]
    system["as_role"](role)
    response = request(
        system,
        "POST",
        "/corrections",
        json={
            "dataset_id": 31,
            "admission_id": current["admission_id"],
            "expected_revision": 1,
            "operation": "RESULT_CORRECTION",
            "reason": "Đối soát",
            "changes": {"admission_score": "20"},
        },
    )
    assert response.status_code == 403, response.text


def test_withdraw_restore_and_legacy_reconciliation_are_distinct_requests(workflow):
    system = workflow
    register(system)
    assert upload(system, result_file(), "admissions_results")["status"] == "SYNCED"
    enrollment = "applicant_code,program_code,year_label,method_code,expected_revision,enrollment_status,enrollment_date\nTS001,CNTT,2026,THPT,2,CONFIRMED,2026-11-18\n"
    assert upload(system, enrollment, "admissions_enrollment")["status"] == "SYNCED"
    system["as_role"]("ADMISSIONS_MANAGER")
    target = rows(system)[0]
    withdraw = correction(
        system,
        target,
        operation="ENROLLMENT_CORRECTION",
        changes={"enrollment_status": "WITHDRAWN"},
    )
    assert apply(system, withdraw).status_code == 200
    target = rows(system)[0]
    assert target["enrollment_date"] == "2026-11-18"
    restore = correction(
        system,
        target,
        operation="RESTORE_ENROLLMENT",
        changes={"enrollment_status": "CONFIRMED", "enrollment_date": "2026-11-18"},
    )
    assert apply(system, restore).status_code == 200
    # A legacy accepted row remains NULL until a deliberate reconciliation request.
    with system["engine"].begin() as conn:
        conn.execute(
            text(
                "UPDATE app.admission SET enrollment_status=NULL,enrollment_date=NULL WHERE admission_id=:id"
            ),
            {"id": target["admission_id"]},
        )
    target = rows(system)[0]
    reconcile = correction(
        system, target, operation="RECONCILE_LEGACY", changes={"enrollment_status": "PENDING"}
    )
    assert apply(system, reconcile).status_code == 200
    assert rows(system)[0]["enrollment_status"] == "PENDING"


def test_missing_registration_and_revoked_uploader_cannot_write(workflow):
    system = workflow
    register(system)
    bad = upload(system, result_file(program="UNKNOWN"), "admissions_results")
    assert bad["status"] == "BLOCKED", bad
    assert bad["error_code"] == "ADMISSION_REGISTRATION_MISSING"
    # Prepare another valid Gold input, then remove the uploader's database role.
    response = asyncio.run(
        system["request"](
            "POST",
            "/ingestion/uploads",
            data={
                "dataset_id": "31",
                "import_options": json.dumps({"import_type": "admissions_results"}),
            },
            files={"file": ("revoked.csv", result_file().encode())},
        )
    )
    assert response.status_code == 202
    system["ingestion"]()
    _, silver = process(system, response.json()["ingestion_job_id"])
    assert silver["status"] == "SUCCESS"
    with system["engine"].begin() as conn:
        conn.execute(
            text("DELETE FROM app.user_role WHERE app_user_id=:id"),
            {"id": system["accounts"]["ADMISSIONS_STAFF"]},
        )
    assert gold_worker.run_once(system["engine"], "revoked-test")
    assert gold_business_sync.run_once(system["engine"])
    with system["engine"].connect() as conn:
        q = conn.execute(
            text(
                "SELECT q.status,q.error_code FROM app.gold_sync_job q JOIN app.gold_job j ON j.job_id=q.gold_job_id WHERE j.silver_snapshot_id=:id"
            ),
            {"id": silver["result"]["publication"]["snapshot_id"]},
        ).one()
        assert q == ("BLOCKED", "ADMISSION_ACTOR_FORBIDDEN")
        assert conn.execute(text("SELECT revision FROM app.admission")).scalar_one() == 1


def test_missing_revision_is_reported_before_database_sync(workflow):
    system = workflow
    register(system)
    content = result_file().replace("Phiên bản hiện tại,", "").replace("THPT,1,25", "THPT,25")
    response = asyncio.run(
        system["request"](
            "POST",
            "/ingestion/uploads",
            data={
                "dataset_id": "31",
                "import_options": json.dumps({"import_type": "admissions_results"}),
            },
            files={"file": ("no-revision.csv", content.encode())},
        )
    )
    assert response.status_code == 202
    system["ingestion"]()
    assert silver_worker.run_once(system["engine"], "missing-revision-test")
    with system["engine"].connect() as conn:
        row = conn.execute(
            text("SELECT status,result FROM app.auto_pipeline_job WHERE ingestion_job_id=:id"),
            {"id": response.json()["ingestion_job_id"]},
        ).one()
        assert row[0] == "FAILED"
        assert any(e["field"] == "expected_revision" for e in row[1]["error_report"]["issues"])


def test_scoreless_method_and_staff_cannot_change_a_final_result(workflow):
    system = workflow
    register(system, method="XTT")
    content = "applicant_code,program_code,year_label,method_code,expected_revision,admission_score,admission_status\nTS001,CNTT,2026,XTT,1,,ACCEPTED\n"
    synced = upload(system, content, "admissions_results")
    assert synced["status"] == "SYNCED", synced
    assert rows(system)[0]["admission_score"] is None
    invalid = content.replace("XTT,1,,ACCEPTED", "XTT,2,0,ACCEPTED")
    blocked = upload(system, invalid, "admissions_results")
    assert blocked["status"] == "BLOCKED", blocked
    assert blocked["error_code"] == "ADMISSION_METHOD_DOES_NOT_USE_SCORE"


def test_registrations_cannot_bypass_revision_or_change_results_through_legacy_sync(workflow):
    from app.repositories.gold_business_sync_repo import BusinessSyncBlockedError, sync_records

    system = workflow
    register(system)
    assert upload(system, result_file(), "admissions_results")["status"] == "SYNCED"
    with (
        pytest.raises(BusinessSyncBlockedError, match="GOLD_SYNC_ADMISSION_CONTRACT_REQUIRED"),
        system["engine"].begin() as conn,
    ):
        sync_records(
            conn,
            {
                "admission": [
                    {"admission_id": "1", "admission_status": "REJECTED", "_source_system_id": "17"}
                ]
            },
            "not-a-job",
        )
    registration = "applicant_code,full_name,program_code,year_label,method_code,preference_rank,admission_status\nTS001,Nguyễn An,CNTT,2026,THPT,2,\n"
    blocked = upload(system, registration, "admissions_registration")
    assert blocked["status"] == "BLOCKED", blocked
    assert blocked["error_code"] == "ADMISSION_EXPECTED_REVISION_REQUIRED"
    assert rows(system)[0]["revision"] == 2
