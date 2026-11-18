from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine, event, func, insert, select, update
from sqlalchemy.pool import StaticPool

from app.modules.ingestion.business_imports import (
    CONTRACTS,
    ImportOptions,
    ImportValidationError,
    detect_contract,
    fingerprint,
    normalize_row,
    packed,
    resolve_layout,
)
from app.repositories.business_import_repo import (
    CHANGES,
    ImportBlockedError,
    describe_import_plan,
    plan_import_records,
)
from app.repositories.gold_business_sync_repo import IDENTITIES, MODELS, sync_records


@pytest.fixture
def engine():
    db = create_engine(
        "sqlite://", poolclass=StaticPool, execution_options={"schema_translate_map": {"app": None}}
    )

    @event.listens_for(db, "connect")
    def connect(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    for table in MODELS.values():
        table.create(db)
    IDENTITIES.create(db)
    CHANGES.create(db)
    yield db
    db.dispose()


def payload(kind, rows, *, blank_update="preserve", namespace="17"):
    contract = CONTRACTS[kind]
    options = ImportOptions(import_type=kind, blank_update=blank_update)
    mapping = {n: n for row in rows for n in row}
    evidence = {
        "options": options.model_dump(mode="json"),
        "field_mapping": mapping,
        "contract_fingerprint": fingerprint(contract.describe()),
    }
    return {
        contract.entity: [
            {
                "_source_system_id": namespace,
                "_business_import_row": packed(
                    {
                        "contract": evidence,
                        "namespace": namespace,
                        "domain": contract.domain,
                        "locator": f"sheet:Nhân sự:row:{i + 2}",
                        "values": row,
                    }
                ),
            }
            for i, row in enumerate(rows)
        ]
    }


def employee(**changes):
    return {
        "employee_code": "NV001",
        "full_name": "Nguyễn Văn An",
        "employee_status": "ACTIVE",
        "is_lecturer": True,
        "academic_degree": "Tiến sĩ",
        **changes,
    }


def count(connection, entity):
    return connection.execute(select(func.count()).select_from(MODELS[entity])).scalar_one()


def test_plan_employee_is_read_only_and_rejects_changes_after_preview(engine):
    data = payload("hr_employee", [employee()])
    entity, rows = next(iter(data.items()))

    with engine.begin() as conn:
        planner = plan_import_records(conn, entity, rows, lock_rows=False)
        preview = describe_import_plan(planner, len(rows))
        assert preview["tables"] == {
            table: {"inserted": 1, "updated": 0, "unchanged": 0}
            for table in ("employee", "lecturer")
        }
        assert all(count(conn, table) == 0 for table in ("employee", "lecturer"))
        assert conn.execute(select(func.count()).select_from(CHANGES)).scalar_one() == 0
        assert "Nguyễn Văn An" not in packed(preview)
        sync_records(conn, data, "first", expected_plan=preview["plan_fingerprint"])

    with engine.begin() as conn:
        preview = describe_import_plan(plan_import_records(conn, entity, rows, lock_rows=False), 1)
        assert preview["tables"]["lecturer"]["unchanged"] == 1
        conn.execute(update(MODELS["employee"]).values(full_name="Đã cập nhật"))
        with pytest.raises(ImportBlockedError, match="IMPORT_PLAN_STALE"):
            sync_records(conn, data, "stale", expected_plan=preview["plan_fingerprint"])
        assert conn.execute(select(MODELS["employee"].c.full_name)).scalar_one() == "Đã cập nhật"
        assert count(conn, "lecturer") == 1


def test_registration_context_supplies_missing_fields_and_checks_matching_columns():
    contract = CONTRACTS["admissions_registration"]
    options = ImportOptions(
        import_type=contract.name, context={"year_label": "2026", "method_code": "THPT"}
    )
    columns = ["Mã thí sinh", "Mã chương trình", "Năm tuyển sinh"]
    mapping = resolve_layout(contract, columns, options)
    row = dict(zip(columns, ("TS001", "CNTT", ""), strict=True))
    values = normalize_row(contract, row, mapping, options, "csv:row:2")
    assert values["method_code"] == "THPT"
    assert values["year_label"] == "2026"
    row["Năm tuyển sinh"] = "2027"
    with pytest.raises(ImportValidationError) as caught:
        normalize_row(contract, row, mapping, options, "csv:row:2")
    assert caught.value.issues == [
        {
            "code": "IMPORT_CONTEXT_MISMATCH",
            "field": "year_label",
            "locator": "csv:row:2",
            "column": "Năm tuyển sinh",
        }
    ]


def masters(connection):
    connection.execute(
        insert(MODELS["department"]).values(
            department_id=1,
            department_code="PDT",
            department_name="Đào tạo",
            department_type="OFFICE",
            status="ACTIVE",
        )
    )
    connection.execute(
        insert(MODELS["major"]).values(
            major_id=1, major_code="CNTT", major_name="CNTT", department_id=1
        )
    )
    connection.execute(
        insert(MODELS["program"]),
        [
            {
                "program_id": i,
                "program_code": code,
                "program_name": code,
                "major_id": 1,
                "degree_level": "BACHELOR",
            }
            for i, code in enumerate(("CNTT", "QTKD"), 1)
        ],
    )
    connection.execute(
        insert(MODELS["course"]),
        [
            {"course_id": i, "course_code": code, "course_name": code, "credit_hours": 3}
            for i, code in enumerate(("CS001", "CS002"), 1)
        ],
    )
    connection.execute(
        insert(MODELS["admission_year"]).values(
            admission_year_id=1,
            year_label="2026",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            status="ACTIVE",
        )
    )
    connection.execute(
        insert(MODELS["admission_method"]).values(
            admission_method_id=1, method_code="THPT", method_name="THPT", max_score=30
        )
    )


def test_vietnamese_aliases_and_explicit_unknown_mapping():
    contract = CONTRACTS["hr_employee"]
    columns = [" Mã NV ", "Họ Và Tên", "Là giảng viên", "employee_status"]
    options = detect_contract("hr", columns)
    assert options
    assert options.import_type == "hr_employee"
    mapping = resolve_layout(contract, columns, options)
    assert mapping["full_name"] == "Họ Và Tên"
    assert mapping["employee_code"] == " Mã NV "
    with pytest.raises(ImportValidationError, match="IMPORT_MAPPING_AMBIGUOUS"):
        resolve_layout(contract, [*columns, "Họ Tên"], options)
    with pytest.raises(ImportValidationError, match="IMPORT_MAPPING_REQUIRED"):
        resolve_layout(contract, [*columns, "Ghi chú"], options)
    options.ignored_columns = ["Ghi chú"]
    assert resolve_layout(contract, [*columns, "Ghi chú"], options) == mapping


def test_mapping_cannot_reuse_column_or_accept_unknown_target():
    c = CONTRACTS["hr_employee"]
    options = ImportOptions(import_type=c.name, field_mapping={"full_name": "Mã NV"})
    with pytest.raises(ImportValidationError, match="IMPORT_COLUMN_USED_TWICE"):
        resolve_layout(c, ["Mã NV", "Là giảng viên"], options)
    options.field_mapping = {"admin": "Mã NV"}
    with pytest.raises(ImportValidationError, match="IMPORT_MAPPING_INVALID"):
        resolve_layout(c, ["Mã NV", "Là giảng viên"], options)


def test_dates_and_codes_are_not_guessed():
    contract = CONTRACTS["hr_employee"]
    options = ImportOptions(import_type=contract.name)
    mapping = {n: n for n in ("employee_code", "is_lecturer", "date_of_birth")}
    with pytest.raises(ImportValidationError):
        normalize_row(
            contract,
            {"employee_code": 123, "is_lecturer": "Có", "date_of_birth": "01/02/2026"},
            mapping,
            options,
            "row:2",
        )
    options.date_format = "%d/%m/%Y"
    row = normalize_row(
        contract,
        {"employee_code": "00123", "is_lecturer": "Có", "date_of_birth": "01/02/2026"},
        mapping,
        options,
        "row:2",
    )
    assert row["employee_code"] == "00123"
    assert row["date_of_birth"] == date(2026, 2, 1)


def test_employee_lecturer_reimport_update_blank_and_false_preserve(engine):
    with engine.begin() as conn:
        result = sync_records(conn, payload("hr_employee", [employee(phone="0123")]), "job1")
        assert result == {"employee": 1, "lecturer": 1}
        assert result.report["tables"]["employee"]["inserted"] == 1
    with engine.begin() as conn:
        result = sync_records(conn, payload("hr_employee", [employee(phone=None)]), "job2")
        assert result.report["tables"]["employee"]["unchanged"] == 1
        sync_records(
            conn,
            payload(
                "hr_employee",
                [employee(full_name="Nguyễn An mới", is_lecturer=False, academic_degree=None)],
            ),
            "job3",
        )
        assert count(conn, "employee") == count(conn, "lecturer") == 1
        staff = conn.execute(select(MODELS["employee"])).mappings().one()
        lecturer = conn.execute(select(MODELS["lecturer"])).mappings().one()
        assert staff["phone"] == "0123"
        assert staff["full_name"] == "Nguyễn An mới"
        assert staff["employee_id"] == lecturer["employee_id"]
        assert count(conn, "employee") == 1
        assert conn.execute(select(func.count()).select_from(CHANGES)).scalar_one() == 5


def test_missing_required_field_and_conflicting_rows_write_nothing(engine):
    with pytest.raises(ImportBlockedError, match="IMPORT_REQUIRED_FIELD"), engine.begin() as conn:
        sync_records(conn, payload("hr_employee", [employee(full_name=None)]), "job1")
    with pytest.raises(ImportBlockedError, match="IMPORT_CONFLICTING_ROWS"), engine.begin() as conn:
        sync_records(conn, payload("hr_employee", [employee(), employee(full_name="Khác")]), "job2")
    with engine.connect() as conn:
        assert count(conn, "employee") == 0


def test_default_blank_update_preserves_existing_values():
    assert ImportOptions(import_type="hr_employee").blank_update == "preserve"


def test_missing_references_report_every_row_and_roll_back_whole_file(engine):
    with engine.begin() as conn:
        masters(conn)
    rows = [
        {
            "program_code": "CNTT",
            "version": "2026",
            "curriculum_code": "K26",
            "curriculum_name": "Khung 2026",
            "course_code": code,
        }
        for code in ("CS001", "UNKNOWN", "UNKNOWN2")
    ]
    with pytest.raises(ImportBlockedError) as error, engine.begin() as conn:
        sync_records(conn, payload("academic_curriculum", rows), "job1")
    assert (
        len([e for e in error.value.report()["issues"] if e["code"] == "IMPORT_REFERENCE_MISSING"])
        == 2
    )
    with engine.connect() as conn:
        assert count(conn, "curriculum") == count(conn, "curriculum_course") == 0
        assert count(conn, "course") == 2


def test_curriculum_many_courses_idempotent_with_lookup_only_masters(engine):
    with engine.begin() as conn:
        masters(conn)
    rows = [
        {
            "program_code": "CNTT",
            "version": "2026",
            "curriculum_code": "K26",
            "curriculum_name": "Khung 2026",
            "course_code": code,
            "semester_no": 1,
        }
        for code in ("CS001", "CS002")
    ]
    for job in ("job1", "job2"):
        with engine.begin() as conn:
            result = sync_records(conn, payload("academic_curriculum", rows), job)
            assert result == {"curriculum": 1, "curriculum_course": 2}
            assert count(conn, "curriculum") == 1
            assert count(conn, "curriculum_course") == 2
            assert count(conn, "program") == count(conn, "course") == 2


def test_applicant_two_registrations_reimport_and_status_change_is_blocked(engine):
    with engine.begin() as conn:
        masters(conn)
    rows = [
        {
            "applicant_code": "TS001",
            "full_name": "Trần An",
            "program_code": code,
            "year_label": "2026",
            "method_code": "THPT",
            "preference_rank": i,
            "admission_status": "APPLIED",
        }
        for i, code in enumerate(("CNTT", "QTKD"), 1)
    ]
    for job in ("job1", "job2"):
        with engine.begin() as conn:
            result = sync_records(conn, payload("admissions_registration", rows), job)
            assert result == {"applicant": 1, "admission": 2}
            assert count(conn, "applicant") == 1
            assert count(conn, "admission") == 2
    with (
        pytest.raises(ImportBlockedError, match="IMPORT_STATUS_TRANSITION_POLICY_REQUIRED"),
        engine.begin() as conn,
    ):
        sync_records(
            conn,
            payload("admissions_registration", [{**rows[0], "admission_status": "ACCEPTED"}]),
            "job3",
        )


def test_source_conflict_and_evidence_cannot_cross_domain(engine):
    with engine.begin() as conn:
        sync_records(conn, payload("hr_employee", [employee()]), "job1")
    with (
        pytest.raises(ImportBlockedError, match="GOLD_SYNC_SOURCE_CONFLICT"),
        engine.begin() as conn,
    ):
        sync_records(conn, payload("hr_employee", [employee()], namespace="18"), "job2")
    altered = payload("hr_employee", [employee()])
    row = altered[CONTRACTS["hr_employee"].entity][0]
    import json

    envelope = json.loads(row["_business_import_row"])
    envelope["domain"] = "academic"
    row["_business_import_row"] = packed(envelope)
    with pytest.raises(ImportBlockedError), engine.begin() as conn:
        sync_records(conn, altered, "job3")


def test_unique_email_conflicts_are_reported_before_writing(engine):
    with pytest.raises(ImportBlockedError, match="IMPORT_UNIQUE_CONFLICT"), engine.begin() as conn:
        sync_records(
            conn,
            payload(
                "hr_employee",
                [
                    employee(email="same@example.test"),
                    employee(employee_code="NV002", email="same@example.test"),
                ],
            ),
            "job1",
        )
    with engine.connect() as conn:
        assert count(conn, "employee") == 0


def test_conflicting_lecturer_flags_are_rejected(engine):
    with pytest.raises(ImportBlockedError, match="IMPORT_CONFLICTING_ROWS"), engine.begin() as conn:
        sync_records(
            conn,
            payload("hr_employee", [employee(), employee(is_lecturer=False, academic_degree=None)]),
            "job1",
        )


def test_assignment_resolves_existing_codes_without_source_identity_and_reimports(engine):
    with engine.begin() as conn:
        masters(conn)
        conn.execute(
            insert(MODELS["employee"]).values(
                employee_id=99, employee_code="NV001", full_name="An", employee_status="ACTIVE"
            )
        )
        conn.execute(
            insert(MODELS["position"]).values(
                position_id=8, position_code="GV", position_name="Giảng viên"
            )
        )
    row = {
        "assignment_code": "PC001",
        "employee_code": "NV001",
        "department_code": "PDT",
        "position_code": "GV",
        "start_date": "2026-01-01",
        "assignment_status": "ACTIVE",
    }
    for job in ("job1", "job2"):
        with engine.begin() as conn:
            assert sync_records(conn, payload("hr_assignment", [row]), job) == {"assignment": 1}
            assert count(conn, "assignment") == 1
            assignment = conn.execute(select(MODELS["assignment"])).mappings().one()
            assert assignment["employee_id"] == 99
            assert assignment["department_id"] == 1
            assert assignment["position_id"] == 8
    with (
        pytest.raises(ImportBlockedError, match="IMPORT_SYSTEM_DEPARTMENT_ONLY"),
        engine.begin() as conn,
    ):
        sync_records(
            conn, payload("hr_assignment", [{**row, "department_code": "KHOA_CNTT"}]), "job3"
        )


def test_qualification_lookup_is_unambiguous_and_does_not_overwrite_other_awards(engine):
    with engine.begin() as conn:
        conn.execute(
            insert(MODELS["employee"]).values(
                employee_id=1, employee_code="NV001", full_name="An", employee_status="ACTIVE"
            )
        )
        conn.execute(
            insert(MODELS["qualification"]).values(
                qualification_id=1,
                qualification_name="Chứng chỉ A",
                qualification_type="Certificate",
            )
        )
    row = {
        "employee_code": "NV001",
        "qualification_name": "Chứng chỉ A",
        "qualification_type": "Certificate",
        "institution": "Trường A",
        "year_obtained": 2026,
    }
    for job in ("job1", "job2"):
        with engine.begin() as conn:
            assert sync_records(conn, payload("hr_qualification", [row]), job) == {
                "employee_qualification": 1
            }
    with (
        pytest.raises(ImportBlockedError, match="IMPORT_QUALIFICATION_INSTANCE_POLICY_REQUIRED"),
        engine.begin() as conn,
    ):
        sync_records(
            conn, payload("hr_qualification", [{**row, "institution": "Trường B"}]), "job3"
        )
