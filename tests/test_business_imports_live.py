"""Real API/worker imports; the shared fixture creates a separate PostgreSQL database."""

from __future__ import annotations

import asyncio
import json
import os
from io import BytesIO

import pytest
from openpyxl import Workbook
from sqlalchemy import text

from app.core.config import settings
from app.modules.pipeline.repositories.silver import SilverRepository
from app.modules.pipeline.repositories.silver_storage import MinioSilverStore
from app.modules.pipeline.services import gold_business_sync, gold_worker
from app.modules.pipeline.silver.publication import SilverPublicationService
from app.providers import silver_writer_client
from tests.test_auto_pipeline_live import system as system
from tests.test_default_silver_live import automatic as automatic
from tests.test_default_silver_live import process

pytestmark = pytest.mark.skipif(
    os.getenv("AUTO_PIPELINE_LIVE_TEST") != "1", reason="Local PostgreSQL and MinIO required"
)


def upload(system, content, *, options=None, filename="staff.csv"):
    data = {"dataset_id": "31"}
    if options is not None:
        data["import_options"] = json.dumps(options)
    response = asyncio.run(
        system["request"](
            "POST",
            "/ingestion/uploads",
            data=data,
            files={"file": (filename, content.encode() if isinstance(content, str) else content)},
        )
    )
    assert response.status_code == 202, response.text
    system["ingestion"]()
    return response.json()["ingestion_job_id"]


def publish(system, job_id):
    profile, silver = process(system, job_id)
    assert silver["status"] == "SUCCESS", silver
    assert gold_worker.run_once(system["engine"], "import-gold-test")
    assert gold_business_sync.run_once(system["engine"])
    with system["engine"].connect() as conn:
        result = dict(
            conn.execute(text("SELECT * FROM app.gold_sync_job ORDER BY created_at DESC LIMIT 1"))
            .mappings()
            .one()
        )
    return profile, silver, result


def test_staff_vietnamese_file_to_employee_and_lecturer_reimport(automatic):
    system = automatic
    content = "Mã NV,Họ Và Tên,Là giảng viên,Trạng thái nhân viên,Học vị,Email,Số điện thoại\n NV001 ,Nguyễn An,Có,AcTiVe,Tiến sĩ,an@example.test,0012345\n"
    for _ in range(2):
        job = upload(system, content)
        profile, silver, synced = publish(system, job)
        assert profile.entity == "import_hr_employee_v1"
        assert synced["status"] == "SYNCED", synced
        assert synced["result"]["entities"] == {"employee": 1, "lecturer": 1}
        publisher = SilverPublicationService(
            repository=SilverRepository(system["engine"]),
            storage=MinioSilverStore(silver_writer_client(), settings.minio_bucket_silver),
            contracts=profile.contracts,
            scope=profile.scope,
        )
        snapshot = silver["result"]["publication"]["snapshot_id"]
        analytic = publisher.read_snapshot(snapshot)[profile.entity][0]
        assert analytic["employee_code"] == "NV001"
        assert analytic["employee_status"] == "ACTIVE"
        assert analytic["academic_degree"] == "Ph.D."
        assert not {"full_name", "email", "phone", "_business_import_row"} & analytic.keys()
    with system["engine"].connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM app.employee")).scalar_one() == 1
        result = conn.execute(
            text(
                "SELECT e.full_name,e.phone,l.academic_degree FROM app.employee e JOIN app.lecturer l USING(employee_id)"
            )
        ).one()
        assert result == ("Nguyễn An", "0012345", "Ph.D.")
        assert (
            conn.execute(text("SELECT count(*) FROM app.business_import_change")).scalar_one() == 4
        )
    system["user"]["roles"] = ["HR_STAFF"]
    response = asyncio.run(system["request"]("GET", f"/ingestion/jobs/{job}/business-import"))
    assert response.status_code == 200, response.text
    assert response.json()["database_committed"] is True
    assert response.json()["database_result"]["tables"]["lecturer"]["unchanged"] == 1
    system["user"]["roles"] = ["ACADEMIC_STAFF"]
    response = asyncio.run(system["request"]("GET", f"/ingestion/jobs/{job}/business-import"))
    assert response.status_code in {403, 404}


def test_real_excel_file_with_explicit_header_mapping(automatic):
    book = Workbook()
    book.active.append(["Mã nội bộ", "Tên cán bộ", "Là giảng viên", "employee_status", "Ghi chú", "Học vị"])
    book.active.append(["0007", "Trần Bình", True, "ACTIVE", "Giữ trong Bronze", None])
    output = BytesIO()
    book.save(output)
    job = upload(
        automatic,
        output.getvalue(),
        filename="staff.xlsx",
        options={
            "import_type": "hr_employee",
            "field_mapping": {"employee_code": "Mã nội bộ", "full_name": "Tên cán bộ"},
            "ignored_columns": ["Ghi chú"],
        },
    )
    _, _, result = publish(automatic, job)
    assert result["status"] == "SYNCED", result
    with automatic["engine"].connect() as conn:
        assert conn.execute(text("SELECT employee_code,full_name FROM app.employee")).one() == (
            "0007",
            "Trần Bình",
        )


def test_alias_conflicts_saved_to_job_report_and_no_business_writes(automatic):
    job = upload(
        automatic, "Mã NV,Họ Tên,Họ Và Tên,Là giảng viên,employee_status\nNV001,A,B,Có,ACTIVE\n"
    )
    from app.modules.pipeline.services import silver_worker

    assert silver_worker.run_once(automatic["engine"], "import-mapping-test")
    with automatic["engine"].connect() as conn:
        result = conn.execute(
            text(
                "SELECT status,error_code,result FROM app.auto_pipeline_job WHERE ingestion_job_id=:id"
            ),
            {"id": job},
        ).one()
        assert result[0] == "FAILED"
        assert result[1] == "IMPORT_MAPPING_AMBIGUOUS"
        assert result[2]["error_report"]["issues"][0]["columns"] == ["Họ Tên", "Họ Và Tên"]
        assert conn.execute(text("SELECT count(*) FROM app.employee")).scalar_one() == 0


def test_api_contracts_and_mapping_follow_staff_domain(automatic):
    system = automatic
    system["user"]["roles"] = ["HR_STAFF"]
    response = asyncio.run(system["request"]("GET", "/ingestion/import-types"))
    assert response.status_code == 200
    assert {c["domain"] for c in response.json()} == {"hr"}
    response = asyncio.run(
        system["request"](
            "POST",
            "/ingestion/imports/analyze",
            json={
                "domain": "hr",
                "columns": ["Mã NV", "Họ Tên", "Là giảng viên"],
                "options": {"import_type": "hr_employee"},
            },
        )
    )
    assert response.status_code == 200, response.text
    assert response.json()["field_mapping"]["full_name"] == "Họ Tên"
    response = asyncio.run(
        system["request"](
            "POST",
            "/ingestion/imports/analyze",
            json={
                "domain": "admissions",
                "columns": ["Mã thí sinh"],
                "options": {"import_type": "admissions_registration"},
            },
        )
    )
    assert response.status_code == 403


def test_last_table_database_constraint_rolls_back_employee_identity_and_audit(automatic):
    with automatic["engine"].begin() as conn:
        conn.execute(
            text(
                "CREATE FUNCTION app.reject_test_lecturer() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE check_violation USING MESSAGE='test constraint'; END $$"
            )
        )
        conn.execute(
            text(
                "CREATE TRIGGER reject_test_lecturer BEFORE INSERT ON app.lecturer FOR EACH ROW EXECUTE FUNCTION app.reject_test_lecturer()"
            )
        )
    job = upload(automatic, "Mã NV,Họ Tên,Là giảng viên,employee_status\nNV001,An,Có,ACTIVE\n")
    _, _, result = publish(automatic, job)
    assert result["status"] == "BLOCKED", result
    with automatic["engine"].connect() as conn:
        for entity in ("employee", "lecturer", "gold_record_identity", "business_import_change"):
            assert conn.execute(text(f"SELECT count(*) FROM app.{entity}")).scalar_one() == 0


def test_missing_master_is_blocked_with_row_column_report(automatic):
    job = upload(
        automatic,
        "Mã phân công,Mã NV,Mã phòng,Mã chức vụ,Ngày bắt đầu,Trạng thái phân công\nPC001,NV001,PNS,GV,2026-01-01,ACTIVE\n",
    )
    _, _, result = publish(automatic, job)
    assert result["status"] == "BLOCKED", result
    errors = result["error_report"]["issues"]
    assert any(
        e["code"] == "IMPORT_REFERENCE_MISSING" and e["column"] == "Mã NV" and e["locator"]
        for e in errors
    )
    with automatic["engine"].connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM app.assignment")).scalar_one() == 0


@pytest.mark.parametrize(
    ("domain", "kind", "content", "counts"),
    [
        (
            "academic",
            "academic_curriculum",
            "Mã chương trình,Phiên bản,Mã khung,Tên khung,Mã học phần\nCNTT,2026,K26,Khung 2026,CS001\nCNTT,2026,K26,Khung 2026,CS002\n",
            {"curriculum": 1, "curriculum_course": 2},
        ),
        (
            "admissions",
            "admissions_registration",
            "Mã TS,Họ Tên,Mã chương trình,Năm tuyển sinh,Mã phương thức,Nguyện vọng,Trạng thái đăng ký\nTS001,Trần An,CNTT,2026,THPT,1,APPLIED\nTS001,Trần An,QTKD,2026,THPT,2,APPLIED\n",
            {"applicant": 1, "admission": 2},
        ),
    ],
)
def test_department_multi_table_file_and_reimport(automatic, domain, kind, content, counts):
    system = automatic
    with system["engine"].begin() as conn:
        conn.execute(
            text("UPDATE app.dataset SET domain=:domain WHERE dataset_id=31"), {"domain": domain}
        )
        if domain == "admissions":
            role_id = conn.execute(
                text(
                    "INSERT INTO app.role(role_code,role_name) VALUES('ADMIN','Admin') RETURNING role_id"
                )
            ).scalar_one()
            user_id = conn.execute(
                text(
                    "INSERT INTO app.app_user(username,password_hash) VALUES('import_admin','unused') RETURNING app_user_id"
                )
            ).scalar_one()
            conn.execute(
                text("INSERT INTO app.user_role VALUES(:user,:role)"),
                {"user": user_id, "role": role_id},
            )
            system["user"].update(sub=str(user_id), roles=["ADMIN"])
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
                "INSERT INTO app.course(course_code,course_name,credit_hours) VALUES('CS001','CS001',3),('CS002','CS002',3)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO app.admission_year(year_label,start_date,end_date,status) VALUES('2026','2026-01-01','2026-12-31','OPEN')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO app.admission_method(method_code,method_name,max_score) VALUES('THPT','THPT',30)"
            )
        )
    for _ in range(2):
        job = upload(system, content, options={"import_type": kind})
        _, _, synced = publish(system, job)
        assert synced["status"] == "SYNCED", synced
        assert synced["result"]["entities"] == counts
    with system["engine"].connect() as conn:
        for entity, expected in counts.items():
            assert conn.execute(text(f"SELECT count(*) FROM app.{entity}")).scalar_one() == expected
        assert conn.execute(text("SELECT count(*) FROM app.program")).scalar_one() == 2
        assert conn.execute(text("SELECT count(*) FROM app.course")).scalar_one() == 2
        if domain == "admissions":
            metrics = dict(
                conn.execute(
                    text(
                        "SELECT metric,value FROM gold.current_metrics WHERE metric IN ('distinct_applicant_count','registration_count')"
                    )
                ).all()
            )
            assert metrics == {"distinct_applicant_count": 1, "registration_count": 2}
