"""Đồng bộ chi tiết Gold: PK nguồn, FK, upsert, dữ liệu thiếu và transaction thật."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.pool import StaticPool

from app.repositories.gold_business_sync_repo import (
    IDENTITIES,
    MODELS,
    BusinessSyncBlockedError,
    sync_records,
)


@pytest.fixture
def engine():
    database = create_engine(
        "sqlite://", poolclass=StaticPool, execution_options={"schema_translate_map": {"app": None}}
    )

    @event.listens_for(database, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    for name in ("department", "position", "employee", "assignment", "course"):
        MODELS[name].create(database)
    IDENTITIES.create(database)
    yield database
    database.dispose()


def employee(identifier="007", code="NV007", name="Nguyễn An", namespace="17"):
    return {
        "employee_id": identifier,
        "employee_code": code,
        "full_name": name,
        "employee_status": "ACTIVE",
        "_source_system_id": namespace,
    }


def test_gold_rows_upsert_and_preserve_leading_zero_identity(engine):
    with engine.begin() as connection:
        assert sync_records(
            connection, {"employee": [employee(), employee("7", "NV7")]}, "job-1"
        ) == {"employee": 2}
    with engine.begin() as connection:
        rows = (
            connection.execute(
                select(MODELS["employee"]).order_by(MODELS["employee"].c.employee_id)
            )
            .mappings()
            .all()
        )
        assert [r["employee_id"] for r in rows] == [1, 2]
        assert [r["employee_code"] for r in rows] == ["NV007", "NV7"]
        sync_records(connection, {"employee": [employee(name="Nguyễn An mới")]}, "job-2")
        assert (
            connection.execute(
                select(MODELS["employee"].c.full_name).where(MODELS["employee"].c.employee_id == 1)
            ).scalar_one()
            == "Nguyễn An mới"
        )
        assert len(connection.execute(select(IDENTITIES)).all()) == 2


def test_foreign_keys_resolve_by_source_mapping_in_dependency_order(engine):
    payload = {
        "assignment": [
            {
                "assignment_id": "A01",
                "employee_id": "007",
                "department_id": "099",
                "position_id": "P1",
                "start_date": date(2026, 1, 1),
                "is_primary": False,
                "assignment_status": "ACTIVE",
                "_source_system_id": "17",
            }
        ],
        "employee": [employee()],
        "department": [
            {
                "department_id": "099",
                "department_code": "PNS",
                "department_name": "Nhân sự",
                "department_type": "Office",
                "status": "ACTIVE",
                "_source_system_id": "17",
            }
        ],
        "position": [
            {
                "position_id": "P1",
                "position_code": "TP",
                "position_name": "Trưởng phòng",
                "_source_system_id": "17",
            }
        ],
    }
    with engine.begin() as connection:
        connection.execute(
            MODELS["department"]
            .insert()
            .values(
                department_id=1,
                department_code="PNS",
                department_name="Phòng Nhân sự",
                department_type="Office",
                status="ACTIVE",
            )
        )
        counts = sync_records(connection, payload, "job-1")
        assert counts["assignment"] == 1
        row = connection.execute(select(MODELS["assignment"])).mappings().one()
        assert row["employee_id"] == row["department_id"] == row["position_id"] == 1
        sync_records(connection, payload, "job-2")
        assert len(connection.execute(select(MODELS["assignment"])).all()) == 1


def test_no_fake_names_and_no_partial_commit(engine):
    invalid = employee()
    invalid.pop("full_name")
    with pytest.raises(BusinessSyncBlockedError) as error, engine.begin() as connection:
        sync_records(connection, {"employee": [employee("1", "VALID"), invalid]}, "job-1")
    assert error.value.report() == {
        "code": "GOLD_SYNC_REQUIRED_FIELD",
        "entity": "employee",
        "field": "full_name",
    }
    with engine.connect() as connection:
        assert connection.execute(select(MODELS["employee"])).all() == []
        assert connection.execute(select(IDENTITIES)).all() == []


def test_missing_references_never_reuse_arbitrary_database_id(engine):
    with pytest.raises(BusinessSyncBlockedError) as error, engine.begin() as connection:
        sync_records(
            connection,
            {
                "assignment": [
                    {
                        "assignment_id": "1",
                        "employee_id": "1",
                        "department_id": "1",
                        "position_id": "1",
                        "start_date": date(2026, 1, 1),
                        "assignment_status": "ACTIVE",
                        "_source_system_id": "17",
                    }
                ]
            },
            "job-1",
        )
    assert error.value.code == "GOLD_SYNC_REFERENCE_MISSING"
    assert error.value.field == "employee_id"


def test_cross_source_code_collision_does_not_overwrite_person(engine):
    with engine.begin() as connection:
        sync_records(connection, {"employee": [employee()]}, "job-1")
    with pytest.raises(BusinessSyncBlockedError) as error, engine.begin() as connection:
        sync_records(
            connection, {"employee": [employee(name="Người khác", namespace="18")]}, "job-2"
        )
    assert error.value.code == "GOLD_SYNC_SOURCE_CONFLICT"
    with engine.connect() as connection:
        assert (
            connection.execute(select(MODELS["employee"].c.full_name)).scalar_one() == "Nguyễn An"
        )


def test_declared_flat_enrollment_projects_courses_without_fake_students(engine):
    row = {
        "enrollment_id": "E01",
        "student_id": "S01",
        "course_code": "CS001",
        "course_name": "Cơ sở dữ liệu",
        "credits": 3,
        "semester": "HK1",
        "academic_year": "2026",
        "score": 8,
        "_source_system_id": "2",
    }
    with engine.begin() as connection:
        assert sync_records(
            connection, {"file_2": [row, {**row, "enrollment_id": "E02"}]}, "job-1"
        ) == {"course": 1}
        course = connection.execute(select(MODELS["course"])).mappings().one()
        assert course["course_code"] == "CS001"
        assert course["credit_hours"] == 3


@pytest.mark.parametrize("entity", ["file_unknown", "app_user", "user_role", "permission"])
def test_unmapped_and_security_entities_are_rejected(engine, entity):
    with pytest.raises(BusinessSyncBlockedError) as error, engine.begin() as connection:
        sync_records(connection, {entity: [{"_source_system_id": "17"}]}, "job-1")
    assert error.value.code == "GOLD_SYNC_ENTITY_MAPPING_REQUIRED"


def test_business_statuses_match_application_filters(engine):
    with engine.begin() as connection:
        sync_records(
            connection, {"employee": [{**employee(), "employee_status": "Active"}]}, "job-1"
        )
        assert (
            connection.execute(select(MODELS["employee"].c.employee_status)).scalar_one()
            == "ACTIVE"
        )


def system_department(code="PNS", source_id="099", namespace="17"):
    return {
        "department_id": source_id,
        "department_code": code,
        "department_name": "Tên từ file không được ghi đè",
        "department_type": "Faculty",
        "status": "INACTIVE",
        "parent_department_id": "unknown-parent",
        "_source_system_id": namespace,
    }


def seed_system_department(connection):
    connection.execute(
        MODELS["department"]
        .insert()
        .values(
            department_id=3,
            department_code="PNS",
            department_name="Phòng Nhân sự",
            department_type="Office",
            status="ACTIVE",
        )
    )


def test_upload_can_only_map_existing_system_department_without_changing_it(engine):
    with engine.begin() as connection:
        seed_system_department(connection)
        before = dict(connection.execute(select(MODELS["department"])).mappings().one())
        for namespace in ("17", "18"):
            sync_records(
                connection, {"department": [system_department(namespace=namespace)]}, "job-1"
            )
        after = dict(connection.execute(select(MODELS["department"])).mappings().one())
        assert before == after
        identities = connection.execute(select(IDENTITIES)).mappings().all()
        assert len(identities) == 2
        assert all(row["target_key"] == {"department_id": 3} for row in identities)


@pytest.mark.parametrize("code", ["UNI", "KCNTT", "BMCNTT", "HR", "", None, 3])
def test_source_departments_never_create_system_departments(engine, code):
    with pytest.raises(BusinessSyncBlockedError) as error, engine.begin() as connection:
        sync_records(connection, {"department": [system_department(code=code)]}, "job-1")
    assert error.value.code == "GOLD_SYNC_SYSTEM_DEPARTMENT_ONLY"
    with engine.connect() as connection:
        assert connection.execute(select(MODELS["department"])).all() == []
        assert connection.execute(select(IDENTITIES)).all() == []


def test_missing_system_department_is_not_created_from_upload(engine):
    with pytest.raises(BusinessSyncBlockedError) as error, engine.begin() as connection:
        sync_records(connection, {"department": [system_department()]}, "job-1")
    assert error.value.code == "GOLD_SYNC_SYSTEM_DEPARTMENT_MISSING"


def test_source_id_cannot_be_remapped_to_a_different_system_department(engine):
    with engine.begin() as connection:
        seed_system_department(connection)
        connection.execute(
            MODELS["department"]
            .insert()
            .values(
                department_id=2,
                department_code="PDT",
                department_name="Phòng Đào tạo",
                department_type="Office",
                status="ACTIVE",
            )
        )
        sync_records(connection, {"department": [system_department()]}, "job-1")
    with pytest.raises(BusinessSyncBlockedError) as error, engine.begin() as connection:
        sync_records(connection, {"department": [system_department(code="PDT")]}, "job-2")
    assert error.value.code == "GOLD_SYNC_DEPARTMENT_MAPPING_CONFLICT"
    with engine.connect() as connection:
        assert connection.execute(select(IDENTITIES.c.target_key)).scalar_one() == {
            "department_id": 3
        }
