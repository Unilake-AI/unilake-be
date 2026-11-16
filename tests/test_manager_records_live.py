"""Role/phân công thật + upload → Gold → business DB → API manager từng phòng."""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from app.modules.manager.router import router
from app.modules.pipeline.services import gold_business_sync, gold_worker
from tests.test_auto_pipeline_live import system as system
from tests.test_default_silver_live import automatic as automatic
from tests.test_default_silver_live import process

pytestmark = pytest.mark.skipif(
    os.getenv("AUTO_PIPELINE_LIVE_TEST") != "1", reason="Cần PostgreSQL và MinIO local"
)


@pytest.mark.parametrize(
    ("domain", "code", "role", "entity", "content", "search"),
    [
        (
            "hr",
            "PNS",
            "HR_MANAGER",
            "employee",
            "employee_id,employee_code,full_name,gender,hire_date,employee_status,date_of_birth,email\n007,NV0007,Nhân sự 100%_A,Male,2020-01-01,Active,1990-01-01,private@example.test\n008,NV0008,Nhân sự 100xxXA,Female,2021-01-01,Active,1991-01-01,other@example.test\n",
            "100%_A",
        ),
        (
            "academic",
            "PDT",
            "ACADEMIC_MANAGER",
            "course",
            "course_id,course_code,course_name,credit_hours,status\n007,CS001,Học phần 100%_A,3,Active\n008,CS002,Học phần 100xxXA,3,Active\n",
            "100%_A",
        ),
        (
            "admissions",
            "PTS",
            "ADMISSIONS_MANAGER",
            "admission_method",
            "admission_method_id,method_code,method_name,max_score,is_active\n007,THPT,Phương thức 100%_A,30,true\n008,HB,Phương thức 100xxXA,30,true\n",
            "100%_A",
        ),
    ],
)
def test_real_business_records_are_scoped_to_current_synced_gold(
    automatic, domain, code, role, entity, content, search
):
    system = automatic
    engine = system["engine"]
    system["app"].include_router(router)
    with engine.begin() as connection:
        department = connection.execute(
            text("SELECT department_id FROM app.department WHERE department_code=:code"),
            {"code": code},
        ).scalar_one()
        other = connection.execute(
            text(
                "SELECT department_id FROM app.department WHERE department_code<>:code ORDER BY department_id LIMIT 1"
            ),
            {"code": code},
        ).scalar_one()
        connection.execute(
            text(
                "UPDATE app.dataset SET domain=:domain,owner_department_id=:department WHERE dataset_id=31"
            ),
            {"domain": domain, "department": department},
        )
        user_id = connection.execute(
            text(
                "INSERT INTO app.app_user(username,password_hash) VALUES('manager_test','unused') RETURNING app_user_id"
            )
        ).scalar_one()
        role_id = connection.execute(
            text(
                "INSERT INTO app.role(role_code,role_name) VALUES(:role,:role) ON CONFLICT(role_code) DO UPDATE SET role_name=EXCLUDED.role_name RETURNING role_id"
            ),
            {"role": role},
        ).scalar_one()
        connection.execute(
            text("INSERT INTO app.user_role(app_user_id,role_id) VALUES(:user,:role)"),
            {"user": user_id, "role": role_id},
        )
    job_id = system["upload"](content)
    system["ingestion"]()
    profile, silver = process(system, job_id)
    assert silver["status"] == "SUCCESS", silver["result"].get("issues")
    assert profile.entity == entity
    assert gold_worker.run_once(engine, "manager-records-live")

    def get(path):
        return asyncio.run(system["request"]("GET", "/manager/" + path))

    system["user"].update(sub=str(user_id), roles=[role])
    path = f"{domain}/records/{entity}"
    # Tổng hợp Gold xong nhưng chưa ghi DB không được trả hồ sơ thủ công hoặc dữ liệu cũ.
    assert get(path).json()["total"] == 0
    assert get("overview").json()["database_sync"]["queued"] == 1
    assert gold_business_sync.run_once(engine)
    assert get("overview").json()["database_sync"]["synced"] == 1
    response = get(path)
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["total"] == 2
    row = page["items"][0]
    assert row["source"]["dataset_id"] == 31
    assert search == "100%_A"
    assert row["source"]["gold_job_id"]
    assert not {"email", "phone", "date_of_birth", "national_id"}.intersection(row["values"])
    assert get(path + f"/{row['record_id']}").json() == row
    assert get(path + "?limit=1&offset=10").json()["total"] == 2
    assert get(path + "?limit=1&offset=10").json()["items"] == []
    assert get(path + "?search=100%25_A").json()["total"] == 1
    assert get(path + "?search=unknown").json()["total"] == 0
    assert get(path + "?dataset_id=999").json()["total"] == 0
    if entity != "admission_method":
        assert get(path + "?status=active").json()["total"] == 2
        assert get(path + "?status=inactive").json()["total"] == 0
    for owner in (other, None):
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE app.dataset SET owner_department_id=:owner WHERE dataset_id=31"),
                {"owner": owner},
            )
        assert get(path).json()["total"] == 0
        assert get(path + f"/{row['record_id']}").status_code == 404
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE app.dataset SET owner_department_id=:owner WHERE dataset_id=31"),
            {"owner": department},
        )
        connection.execute(
            text("UPDATE app.gold_sync_job SET status='BLOCKED' WHERE gold_job_id=:id"),
            {"id": row["source"]["gold_job_id"]},
        )
    assert get(path).json()["total"] == 0
    assert get("overview").json()["database_sync"]["blocked"] == 1
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE app.gold_sync_job SET status='SYNCED' WHERE gold_job_id=:id"),
            {"id": row["source"]["gold_job_id"]},
        )
    # Một alias nguồn mới ghi đè cùng hồ sơ từ dataset khác phòng: không được
    # dùng identity cũ còn nhìn thấy để trả nội dung của lần ghi không có quyền.
    _overwrite_from_hidden_dataset(engine, domain, entity, row, other, profile.source_system_id)
    assert get(path).json()["total"] == 1
    assert get(path + f"/{row['record_id']}").status_code == 404
    with engine.begin() as connection:
        connection.execute(text("UPDATE app.gold_scope SET generation=generation+1"))
    assert get(path).json()["total"] == 0
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE app.department SET status='INACTIVE' WHERE department_id=:id"),
            {"id": department},
        )
    assert get("context").json()["needs_department_configuration"]
    assert get(path).status_code == 403
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE app.department SET status='ACTIVE' WHERE department_id=:id"),
            {"id": department},
        )
    # Role đủ quyền dù tài khoản chưa gắn employee/phân công. Thu hồi role trong DB
    # phải có hiệu lực ngay cả khi token vẫn giữ role cũ.
    assert not get("context").json()["needs_department_configuration"]
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM app.user_role WHERE app_user_id=:id"), {"id": user_id})
    assert get(path).status_code == 403


def _overwrite_from_hidden_dataset(engine, domain, entity, record, owner, namespace):
    from app.repositories.gold_business_sync_repo import MODELS, sync_records

    job, snapshot, scope = str(uuid4()), str(uuid4()), "production:hidden-" + uuid4().hex
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO app.dataset(dataset_id,dataset_code,dataset_name,domain,data_layer_id,owner_department_id) OVERRIDING SYSTEM VALUE VALUES(32,'hidden','Dataset phòng khác',:domain,1,:owner)"
            ),
            {"domain": domain, "owner": owner},
        )
        args = {"job": job, "snapshot": snapshot, "scope": scope, "domain": domain}
        connection.execute(
            text(
                "INSERT INTO app.gold_scope(scope,dataset_id,domain,silver_snapshot_id) VALUES(:scope,32,:domain,:snapshot)"
            ),
            args,
        )
        connection.execute(
            text(
                "INSERT INTO app.gold_job(job_id,scope,dataset_id,domain,generation,silver_snapshot_id,status,finished_at) VALUES(:job,:scope,32,:domain,1,:snapshot,'SUCCEEDED',now())"
            ),
            args,
        )
        connection.execute(
            text("UPDATE app.gold_scope SET active_job_id=:job WHERE scope=:scope"), args
        )
        connection.execute(
            text(
                "UPDATE app.gold_sync_job SET status='SYNCED',finished_at=now() WHERE gold_job_id=:job"
            ),
            args,
        )
        table = MODELS[entity]
        primary = next(iter(table.primary_key))
        values = dict(
            connection.execute(select(table).where(primary == record["record_id"])).mappings().one()
        )
        values[primary.name] = "new-source-alias"
        values["_source_system_id"] = str(namespace)
        for field in ("full_name", "course_name", "method_name"):
            if field in values:
                values[field] = "Nội dung thuộc phòng khác"
        assert sync_records(connection, {entity: [values]}, job) == {entity: 1}
