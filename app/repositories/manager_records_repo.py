"""Đọc bản ghi nghiệp vụ do Gold hiện hành đã đồng bộ, trong scope manager."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import text

from app.core.exceptions import AppException
from app.modules.manager.schemas import (
    Domain,
    ManagerRecord,
    RecordFilters,
    RecordPage,
    RecordSource,
    RecordType,
    ReportFilters,
)
from app.repositories.manager_report_repo import dataset_scope

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.modules.manager.access import ManagerAccess

# Tên bảng/cột chỉ đến từ allowlist này, không nội suy identifier do client gửi.
FIELDS = {
    "employee": "employee_id employee_code full_name gender hire_date employee_status",
    "department": "department_id department_code department_name department_type parent_department_id status",
    "position": "position_id position_code position_name position_level",
    "assignment": "assignment_id employee_id department_id position_id start_date end_date is_primary assignment_status",
    "lecturer": "employee_id academic_degree academic_title research_field",
    "qualification": "qualification_id qualification_name qualification_type",
    "student": "student_id student_code full_name gender program_id admission_year_id student_status",
    "course": "course_id course_code course_name credit_hours status",
    "class": "class_id class_code course_id semester_id max_capacity room class_status",
    "class_lecturer": "class_lecturer_id class_id lecturer_id teaching_role is_primary assigned_from assigned_to",
    "enrollment": "enrollment_id student_id class_id enrollment_date enrollment_status",
    "enrollment_grade": "enrollment_grade_id enrollment_id midterm_score final_score total_score grade_value letter_grade registration_date",
    "semester": "semester_id semester_code academic_year start_date end_date status",
    "curriculum": "curriculum_id program_id curriculum_code curriculum_name version effective_from effective_to status",
    "curriculum_course": "curriculum_course_id curriculum_id course_id semester_no course_type is_required prerequisite_course_id",
    "program": "program_id program_code program_name major_id degree_level duration_years tuition_fee_per_credit status",
    "major": "major_id major_code major_name department_id status",
    "applicant": "applicant_id full_name gender high_school_name province",
    "admission": "admission_id applicant_id program_id preference_rank admission_method_id admission_year_id application_date admission_score admission_status",
    "admission_year": "admission_year_id year_label start_date end_date status",
    "admission_method": "admission_method_id method_code method_name max_score description is_active",
}
ENTITIES = {
    "hr": ("employee", "department", "position", "assignment", "lecturer", "qualification"),
    "academic": (
        "student",
        "course",
        "class",
        "class_lecturer",
        "enrollment",
        "enrollment_grade",
        "semester",
        "program",
        "major",
        "curriculum",
        "curriculum_course",
    ),
    "admissions": (
        "applicant",
        "admission",
        "program",
        "major",
        "admission_year",
        "admission_method",
    ),
}


def describe(entity: str) -> RecordType:
    fields = FIELDS[entity].split()
    return RecordType(
        entity=entity,
        primary_key=fields[0],
        fields=fields,
        search_fields=[
            field
            for field in fields
            if field.endswith(("_code", "_name")) or field in {"academic_year", "year_label"}
        ],
        status_field=next((f for f in fields if f == "status" or f.endswith("_status")), None),
    )


def record_types(access: ManagerAccess, domain: Domain) -> list[RecordType]:
    access.require_scope(domain)
    return [describe(entity) for entity in ENTITIES[domain]]


async def records(
    db: AsyncSession,
    access: ManagerAccess,
    domain: Domain,
    entity: str,
    filters: RecordFilters,
    *,
    record_id: int | None = None,
) -> RecordPage:
    access.require_scope(domain)
    if entity not in ENTITIES[domain]:
        raise AppException(
            "Loại dữ liệu không thuộc phân hệ này", code="MANAGER_ENTITY_NOT_FOUND", status_code=404
        )
    spec = describe(entity)
    if filters.status is not None and spec.status_field is None:
        raise AppException(
            "Loại dữ liệu không có trường trạng thái",
            code="MANAGER_STATUS_UNSUPPORTED",
            status_code=422,
        )
    if filters.search is not None and not spec.search_fields:
        raise AppException(
            "Loại dữ liệu không hỗ trợ tìm theo tên hoặc mã",
            code="MANAGER_SEARCH_UNSUPPORTED",
            status_code=422,
        )
    clause, params = dataset_scope(
        access, ReportFilters(domain=domain, dataset_id=filters.dataset_id)
    )
    params.update(
        entity=entity,
        requested_domain=domain,
        record_id=record_id,
        limit=filters.limit,
        offset=filters.offset,
    )
    columns = ",".join('r."' + name + '"' for name in spec.fields)
    primary = 'r."' + spec.primary_key + '"'
    query = f"""WITH latest_identity AS (
      SELECT DISTINCT ON (entity,target_fingerprint) * FROM app.gold_record_identity
      WHERE entity=:entity ORDER BY entity,target_fingerprint,updated_at DESC,last_gold_job_id DESC,source_key
    ), visible_records AS (
      SELECT {primary} AS record_id,{columns},d.dataset_id,d.dataset_name,
             j.job_id AS gold_job_id,j.silver_snapshot_id,j.finished_at AS published_at,q.finished_at AS synced_at
      FROM latest_identity i
      JOIN app."{entity}" r ON CAST({primary} AS text)=i.target_key->>'{spec.primary_key}'
      JOIN app.gold_job j ON j.job_id=i.last_gold_job_id
      JOIN app.gold_scope s ON s.scope=j.scope AND s.active_job_id=j.job_id
           AND s.generation=j.generation AND s.silver_snapshot_id=j.silver_snapshot_id
      JOIN app.gold_sync_job q ON q.gold_job_id=j.job_id AND q.status='SYNCED'
      JOIN app.dataset d ON d.dataset_id=j.dataset_id AND d.domain=j.domain
      WHERE j.status='SUCCEEDED' AND j.domain=:requested_domain AND {clause}
    ) SELECT * FROM visible_records WHERE (CAST(:record_id AS bigint) IS NULL OR record_id=:record_id)"""
    if filters.search is not None:
        # Tìm đúng nội dung nhập; không coi %, _ là ký tự đại diện.
        params["search"] = (
            "%" + filters.search.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
        )
        query += (
            " AND ("
            + " OR ".join('"' + f + "\" ILIKE :search ESCAPE '!'" for f in spec.search_fields)
            + ")"
        )
    if filters.status is not None:
        params["status"] = filters.status.upper()
        query += f' AND upper("{spec.status_field}")=:status'
    # Đếm cùng tập dữ liệu sau lọc; offset vượt cuối trang vẫn giữ total.
    total = (
        await db.execute(text("SELECT count(*) FROM (" + query + ") matched"), params)
    ).scalar_one()
    result = (
        await db.execute(text(query + " ORDER BY record_id LIMIT :limit OFFSET :offset"), params)
    ).mappings()
    return RecordPage(
        domain=domain,
        entity=entity,
        total=total,
        limit=filters.limit,
        offset=filters.offset,
        items=[
            ManagerRecord(
                record_id=row["record_id"],
                values={field: row[field] for field in spec.fields},
                source=RecordSource.model_validate(row),
            )
            for row in result
        ],
    )
