"""Real API/worker imports; the shared fixture creates a separate PostgreSQL database."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import date
from decimal import Decimal
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


@pytest.fixture(autouse=True)
def import_actor(automatic, monkeypatch, tmp_path):
    from app.modules.ingestion.services import worker as ingestion_worker
    from app.modules.pipeline.routers import gold as gold_api
    from app.modules.pipeline.services import silver_worker

    application = automatic["app"]
    application.include_router(gold_api.router)
    application.dependency_overrides[gold_api.gold_engine] = lambda: automatic["engine"]
    for worker in (ingestion_worker, silver_worker, gold_worker):
        monkeypatch.setattr(worker, "HEALTH_FILE", tmp_path / worker.HEALTH_FILE.name)
    with automatic["engine"].begin() as conn:
        role_id = conn.execute(
            text("""
                INSERT INTO app.role(role_code, role_name) VALUES ('DATA_ADMIN','Data Admin')
                ON CONFLICT (role_code) DO UPDATE SET role_code=EXCLUDED.role_code
                RETURNING role_id
            """)
        ).scalar_one()
        user_id = conn.execute(
            text("""
                INSERT INTO app.app_user(username,password_hash)
                VALUES ('stage_two_actor','unused') RETURNING app_user_id
            """)
        ).scalar_one()
        conn.execute(
            text("INSERT INTO app.user_role(app_user_id,role_id) VALUES (:user,:role)"),
            {"user": user_id, "role": role_id},
        )
    automatic["user"]["sub"] = str(user_id)


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
    assert silver["status"] == "SUCCESS", (
        silver.get("error_code"),
        (silver.get("result") or {}).get("error_report"),
    )
    assert gold_worker.run_once(system["engine"], "import-gold-test")
    assert gold_business_sync.run_once(system["engine"])
    with system["engine"].connect() as conn:
        result = dict(
            conn.execute(text("SELECT * FROM app.gold_sync_job ORDER BY created_at DESC LIMIT 1"))
            .mappings()
            .one()
        )
    return profile, silver, result


def import_csv(system, kind, content):
    job = upload(system, content, filename=f"{kind}.csv", options={"import_type": kind})
    return publish(system, job)[2]


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
    url = f"/ingestion/jobs/{job}/business-import/preview"
    preview = asyncio.run(system["request"]("GET", url))
    assert preview.status_code == 200, preview.text
    assert preview.json()["status"] == "READY"
    assert preview.json()["tables"]["lecturer"]["unchanged"] == 1
    assert "Nguyễn An" not in preview.text
    assert "plan_ref" not in preview.json()
    system["user"]["roles"] = ["ACADEMIC_STAFF"]
    response = asyncio.run(system["request"]("GET", f"/ingestion/jobs/{job}/business-import"))
    assert response.status_code in {403, 404}
    assert asyncio.run(system["request"]("GET", url)).status_code in {403, 404}


def test_real_excel_file_with_explicit_header_mapping(automatic):
    book = Workbook()
    book.active.append(
        ["Mã nội bộ", "Tên cán bộ", "Là giảng viên", "employee_status", "Ghi chú", "Học vị"]
    )
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
    assert {c["import_type"] for c in response.json()} >= {
        "hr_position_catalog",
        "hr_qualification_catalog",
    }
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


def test_preparation_blocks_unresolved_catalog_without_domain_writes(automatic):
    with automatic["engine"].begin() as conn:
        conn.execute(text("UPDATE app.dataset SET domain='academic' WHERE dataset_id=31"))
    job = upload(
        automatic,
        "Mã chương trình,Phiên bản,Mã khung,Tên khung,Mã học phần\nCNTT,2026,K26,Khung,CS001\n",
    )
    _, _, result = publish(automatic, job)
    response = asyncio.run(
        automatic["request"]("GET", f"/ingestion/jobs/{job}/business-import/preview")
    )
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["status"] == preview["database_status"] == "BLOCKED"
    assert preview["database_committed"] is False
    assert "plan_ref" not in result["preparation"]
    assert {
        error["column"]
        for error in preview["error_report"]["issues"]
        if error["code"] == "IMPORT_REFERENCE_MISSING" and error["locator"]
    } == {"Mã chương trình", "Mã học phần"}
    with automatic["engine"].connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM app.curriculum")).scalar_one() == 0


def test_preparation_checks_uploader_account_again(automatic):
    job = upload(
        automatic,
        "Mã NV,Họ Tên,Là giảng viên,Trạng thái nhân viên\nNV001,Nguyễn An,Có,ACTIVE\n",
    )
    with automatic["engine"].begin() as conn:
        conn.execute(
            text("UPDATE app.app_user SET is_active=false WHERE app_user_id=:id"),
            {"id": int(automatic["user"]["sub"])},
        )
    _, _, result = publish(automatic, job)
    assert result["status"] == "BLOCKED"
    assert result["preparation"]["preview"]["error_report"]["code"] == "IMPORT_ACTOR_FORBIDDEN"
    with automatic["engine"].connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM app.employee")).scalar_one() == 0


def test_retry_replans_when_postgres_changes_after_preview(automatic, monkeypatch):
    job = upload(
        automatic,
        "Mã NV,Họ Tên,Là giảng viên,Trạng thái nhân viên\nNV001,Nguyễn An,Có,ACTIVE\n",
    )
    prepare = gold_business_sync._prepare_import

    def add_employee_after_preview(*args, **kwargs):
        fingerprint = prepare(*args, **kwargs)
        with automatic["engine"].begin() as conn:
            conn.execute(
                text("""
                INSERT INTO app.employee(employee_code,full_name,employee_status)
                VALUES ('NV001','Bản ghi đến song song','ACTIVE')
            """)
            )
        return fingerprint

    with monkeypatch.context() as patch:
        patch.setattr(gold_business_sync, "_prepare_import", add_employee_after_preview)
        _, _, result = publish(automatic, job)
    assert result["status"] == "BLOCKED"
    assert result["error_code"] == "IMPORT_PLAN_STALE"
    assert result["preparation"]["preview"]["tables"]["employee"]["inserted"] == 1
    with automatic["engine"].connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM app.lecturer")).scalar_one() == 0

    url = f"/pipeline/gold/jobs/{result['gold_job_id']}/database-sync/retry"
    response = asyncio.run(automatic["request"]("POST", url))
    assert response.status_code == 202, response.text
    assert gold_business_sync.run_once(automatic["engine"])
    with automatic["engine"].connect() as conn:
        status, preparation = conn.execute(
            text("SELECT status,preparation FROM app.gold_sync_job WHERE gold_job_id=:id"),
            {"id": result["gold_job_id"]},
        ).one()
        assert status == "SYNCED"
        assert preparation["preview"]["tables"]["employee"]["updated"] == 1
        assert conn.execute(text("SELECT count(*) FROM app.lecturer")).scalar_one() == 1


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
            "Mã TS,Họ Tên,Mã chương trình,Nguyện vọng,Trạng thái đăng ký\nTS001,Trần An,CNTT,1,APPLIED\nTS001,Trần An,QTKD,2,APPLIED\n",
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
    context = {"year_label": "2026", "method_code": "THPT"} if domain == "admissions" else {}
    for attempt in range(2):
        job = upload(system, content, options={"import_type": kind, "context": context})
        _, _, synced = publish(system, job)
        assert synced["status"] == "SYNCED", synced
        assert synced["result"]["entities"] == counts
        preview = synced["preparation"]["preview"]
        assert preview["context"] == context
        action = "inserted" if attempt == 0 else "unchanged"
        assert {table: entries[action] for table, entries in preview["tables"].items()} == counts
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


def test_academic_records_import_in_dependency_order_and_reuse_database_keys(automatic):
    system = automatic
    with system["engine"].begin() as conn:
        conn.execute(text("UPDATE app.dataset SET domain='academic' WHERE dataset_id=31"))
        conn.execute(
            text("""
                WITH major AS (
                    INSERT INTO app.major(major_code,major_name,department_id)
                    SELECT 'CNTT','CNTT',department_id FROM app.department WHERE department_code='PDT'
                    RETURNING major_id
                )
                INSERT INTO app.program(program_code,program_name,major_id,degree_level)
                SELECT 'CNTT','Công nghệ thông tin',major_id,'BACHELOR' FROM major
            """)
        )
        conn.execute(
            text("""
                INSERT INTO app.admission_year(year_label,start_date,end_date,status)
                VALUES ('2026','2026-01-01','2026-12-31','OPEN');
                INSERT INTO app.course(course_code,course_name,credit_hours) VALUES ('CS001','Cơ sở',3);
                INSERT INTO app.semester(semester_code,academic_year,start_date,end_date,status)
                VALUES ('2026-1','2026-2027','2026-09-01','2026-12-31','Completed')
            """)
        )

    def import_file(kind, content):
        return import_csv(system, kind, content)

    students = (
        "MSSV,Họ và tên,Mã chương trình,Năm nhập học,Trạng thái sinh viên\n"
        "2026010001,Nguyễn An,CNTT,2026,Đang học\n"
        "2026010002,Trần Bình,CNTT,2026,Đang học\n"
    )
    classes = (
        "Mã lớp học phần,Mã môn,Mã học kỳ,Sĩ số tối đa,Trạng thái lớp\n"
        "CS001-2026-1-01,CS001,2026-1,40,Đã hoàn thành\n"
    )
    enrollment = (
        "Mã SV,Mã lớp,Ngày đăng ký,Trạng thái đăng ký\n"
        "2026010001,CS001-2026-1-01,2026-08-21,Đã hoàn thành\n"
        "2026010002,CS001-2026-1-01,2026-08-22,Registered\n"
    )
    grades = (
        "Mã SV,Mã lớp,Điểm giữa kỳ,Điểm tổng kết,Điểm hệ 4,Điểm chữ,Ngày đăng ký\n"
        "2026010001,CS001-2026-1-01,7,8.2,3.5,b+,2026-08-21\n"
        "2026010002,CS001-2026-1-01,0,4.9,1,D,\n"
    )
    for kind, content, table, total in (
        ("academic_student", students, "student", 2),
        ("academic_class", classes, "class", 1),
    ):
        result = import_file(kind, content)
        assert result["status"] == "SYNCED", result
        assert result["result"]["entities"] == {table: total}

    before_enrollment = import_file("academic_enrollment_grade", grades)
    assert before_enrollment["status"] == "BLOCKED", before_enrollment
    assert before_enrollment["error_code"] == "IMPORT_ACADEMIC_ENROLLMENT_REQUIRED"
    assert [item["column"] for item in before_enrollment["error_report"]["issues"]] == [
        "Mã lớp"
    ] * 2

    late_enrollment = import_file(
        "academic_enrollment", enrollment.replace("2026-08-22", "2027-01-01")
    )
    assert late_enrollment["status"] == "BLOCKED", late_enrollment
    assert late_enrollment["error_code"] == "IMPORT_DATE_RANGE_INVALID"
    with system["engine"].connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM app.enrollment")).scalar_one() == 0

    result = import_file("academic_enrollment", enrollment)
    assert result["status"] == "SYNCED", result
    assert result["result"]["entities"] == {"enrollment": 2}

    invalid_grades = import_file(
        "academic_enrollment_grade",
        grades.replace("2026-08-21", "2026-08-22").replace("4.9,1,D", "4.9,5,D"),
    )
    assert invalid_grades["status"] == "BLOCKED", invalid_grades
    assert {
        issue["code"]: issue["column"] for issue in invalid_grades["error_report"]["issues"]
    } == {
        "IMPORT_GRADE_REGISTRATION_DATE_MISMATCH": "Ngày đăng ký",
        "IMPORT_VALUE_OUT_OF_RANGE": "Điểm hệ 4",
    }
    with system["engine"].connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM app.enrollment_grade")).scalar_one() == 0

    result = import_file("academic_enrollment_grade", grades)
    assert result["status"] == "SYNCED", result
    assert result["result"]["entities"] == {"enrollment_grade": 2}
    repeated = import_file("academic_enrollment_grade", grades)
    assert repeated["status"] == "SYNCED", repeated
    assert repeated["preparation"]["preview"]["tables"]["enrollment_grade"]["unchanged"] == 2
    correction = import_file(
        "academic_enrollment_grade", grades.replace(",7,8.2,3.5,b+", ",,8.7,4,A")
    )
    assert correction["status"] == "SYNCED", correction
    assert correction["preparation"]["preview"]["tables"]["enrollment_grade"]["updated"] == 1

    with system["engine"].connect() as conn:
        records = conn.execute(
            text("""
                SELECT s.student_code, s.student_status, s.admission_id, s.applicant_id,
                       c.class_status, e.enrollment_status, g.midterm_score, g.total_score,
                       g.letter_grade, g.registration_date
                FROM app.enrollment_grade g
                JOIN app.enrollment e USING (enrollment_id)
                JOIN app.student s USING (student_id)
                JOIN app.class c USING (class_id)
                ORDER BY s.student_code
            """)
        ).all()
    assert records == [
        (
            "2026010001",
            "Active",
            None,
            None,
            "Completed",
            "Completed",
            7,
            Decimal("8.7"),
            "A",
            date(2026, 8, 21),
        ),
        (
            "2026010002",
            "Active",
            None,
            None,
            "Completed",
            "Registered",
            0,
            Decimal("4.9"),
            "D",
            None,
        ),
    ]


def test_hr_catalogs_are_reused_by_assignment_and_employee_qualification(automatic):
    system = automatic
    positions = "Mã chức vụ,Tên chức vụ,Cấp chức vụ\nCV-QT,Chuyên viên quản trị,Nhân viên\n"
    qualifications = "Tên văn bằng,Loại văn bằng\nChứng chỉ Kiểm thử,Chứng chỉ\n"
    for kind, table, content in (
        ("hr_position_catalog", "position", positions),
        ("hr_qualification_catalog", "qualification", qualifications),
    ):
        result = import_csv(system, kind, content)
        assert result["status"] == "SYNCED", result
        assert result["result"]["entities"] == {table: 1}
        replay = import_csv(system, kind, content)
        assert replay["status"] == "SYNCED", replay
        assert replay["preparation"]["preview"]["tables"][table]["unchanged"] == 1

    for kind, content in (
        (
            "hr_employee",
            "Mã NV,Họ Tên,Là giảng viên,Trạng thái nhân viên\nNV-CAT,Nguyễn An,Không,ACTIVE\n",
        ),
        (
            "hr_assignment",
            "Mã phân công,Mã NV,Mã phòng,Mã chức vụ,Ngày bắt đầu,Trạng thái phân công\n"
            "PC-CAT,NV-CAT,PNS,CV-QT,2026-01-01,ACTIVE\n",
        ),
        (
            "hr_qualification",
            "Mã NV,Tên chứng chỉ,Loại chứng chỉ,Cơ sở cấp,Năm cấp\n"
            "NV-CAT,Chứng chỉ Kiểm thử,Chứng chỉ,Trường A,2026\n",
        ),
    ):
        result = import_csv(system, kind, content)
        assert result["status"] == "SYNCED", result

    change = import_csv(
        system, "hr_qualification_catalog", qualifications.replace(",Chứng chỉ\n", ",Bằng cấp\n")
    )
    assert change["status"] == "BLOCKED", change
    assert change["error_code"] == "IMPORT_QUALIFICATION_TYPE_IN_USE"
    assert change["error_report"]["issues"][0]["column"] == "Loại văn bằng"
    with system["engine"].connect() as conn:
        linked = conn.execute(
            text("""
            SELECT p.position_code, p.position_level, q.qualification_name, q.qualification_type
            FROM app.assignment a JOIN app.position p USING (position_id)
            JOIN app.employee_qualification eq USING (employee_id)
            JOIN app.qualification q USING (qualification_id)
        """)
        ).all()
    assert linked == [("CV-QT", "Staff", "Chứng chỉ Kiểm thử", "Certificate")]


def test_admissions_catalogs_support_registration_and_protect_published_scores(automatic):
    system = automatic
    with system["engine"].begin() as conn:
        conn.execute(text("UPDATE app.dataset SET domain='admissions' WHERE dataset_id=31"))
        conn.execute(
            text("""
            WITH major AS (
                INSERT INTO app.major(major_code,major_name,department_id)
                SELECT 'CNTT','CNTT',department_id FROM app.department WHERE department_code='PDT'
                RETURNING major_id
            )
            INSERT INTO app.program(program_code,program_name,major_id,degree_level)
            SELECT 'CNTT','CNTT',major_id,'BACHELOR' FROM major
        """)
        )
    years = (
        "Năm tuyển sinh,Ngày bắt đầu,Ngày kết thúc,Trạng thái\n2026,2026-01-01,2026-12-31,Đang mở\n"
    )
    methods = (
        "Mã phương thức,Tên phương thức,Thang điểm,Đang áp dụng\nTHPT-CAT,Điểm thi THPT,30,Có\n"
    )
    for kind, table, content in (
        ("admissions_year_catalog", "admission_year", years),
        ("admissions_method_catalog", "admission_method", methods),
    ):
        result = import_csv(system, kind, content)
        assert result["status"] == "SYNCED", result
        assert result["result"]["entities"] == {table: 1}
        replay = import_csv(system, kind, content)
        assert replay["status"] == "SYNCED", replay
        assert replay["preparation"]["preview"]["tables"][table]["unchanged"] == 1

    overlap = import_csv(system, "admissions_year_catalog", years.replace("\n2026,", "\n2026-2,"))
    assert overlap["status"] == "BLOCKED", overlap
    assert overlap["error_code"] == "IMPORT_ADMISSION_YEARS_OVERLAP"
    assert overlap["error_report"]["issues"][0]["column"] == "Năm tuyển sinh"

    registration_file = (
        "Mã TS,Họ và tên,Mã chương trình,Năm tuyển sinh,Mã phương thức,Nguyện vọng,Ngày đăng ký,Trạng thái đăng ký\n"
        "TS-CAT,Nguyễn An,CNTT,2026,THPT-CAT,1,2026-07-01,APPLIED\n"
    )
    registration = import_csv(system, "admissions_registration", registration_file)
    assert registration["status"] == "SYNCED", registration

    disabled = import_csv(system, "admissions_method_catalog", methods.replace(",Có\n", ",Không\n"))
    assert disabled["status"] == "SYNCED", disabled
    new_registration = import_csv(
        system, "admissions_registration", registration_file.replace("TS-CAT,", "TS-CAT-NEW,")
    )
    assert new_registration["status"] == "BLOCKED", new_registration
    assert new_registration["error_code"] == "IMPORT_ADMISSION_METHOD_INACTIVE"
    assert new_registration["error_report"]["issues"][0]["column"] == "Mã phương thức"
    replay = import_csv(system, "admissions_registration", registration_file)
    assert replay["status"] == "SYNCED", replay

    results = import_csv(
        system,
        "admissions_results",
        "Mã TS,Mã chương trình,Năm tuyển sinh,Mã phương thức,Phiên bản hiện tại,Điểm xét tuyển,Kết quả xét tuyển\n"
        "TS-CAT,CNTT,2026,THPT-CAT,1,25.5,ACCEPTED\n",
    )
    assert results["status"] == "SYNCED", results

    incompatible = import_csv(system, "admissions_method_catalog", methods.replace(",30,", ",20,"))
    assert incompatible["status"] == "BLOCKED", incompatible
    assert incompatible["error_code"] == "IMPORT_ADMISSION_METHOD_SCORE_IN_USE"
    assert incompatible["error_report"]["issues"][0]["column"] == "Thang điểm"
    with system["engine"].connect() as conn:
        linked = conn.execute(
            text("""
                SELECT y.year_label, m.method_code, m.max_score, m.is_active, a.admission_score
            FROM app.admission a JOIN app.admission_year y USING (admission_year_id)
            JOIN app.admission_method m USING (admission_method_id)
        """)
        ).all()
        assert conn.execute(text("SELECT count(*) FROM app.admission_year")).scalar_one() == 1
        assert conn.execute(text("SELECT count(*) FROM app.applicant")).scalar_one() == 1
    assert linked == [("2026", "THPT-CAT", 30, False, Decimal("25.5"))]
