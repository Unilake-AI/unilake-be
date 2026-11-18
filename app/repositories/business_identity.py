from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Index,
    String,
    Table,
    func,
    insert,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.infra.db.base import Base
from app.modules.users import models as business

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

IDENTITIES = Table(
    "gold_record_identity",
    Base.metadata,
    Column("namespace", String(100), primary_key=True),
    Column("entity", String(100), primary_key=True),
    Column("source_key", String(1024), primary_key=True),
    Column("target_key", JSON().with_variant(JSONB(), "postgresql"), nullable=False),
    Column("target_fingerprint", String(64), nullable=False),
    Column("last_gold_job_id", String(36), nullable=False),
    Column("updated_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Index("ix_gold_record_target", "entity", "target_fingerprint"),
    schema="app",
)

MODELS = {
    name: cast("Table", model.__table__)
    for name, model in {
        "department": business.Department,
        "position": business.Position,
        "qualification": business.Qualification,
        "employee": business.Employee,
        "major": business.Major,
        "program": business.Program,
        "course": business.Course,
        "semester": business.Semester,
        "curriculum": business.Curriculum,
        "curriculum_course": business.CurriculumCourse,
        "admission_year": business.AdmissionYear,
        "admission_method": business.AdmissionMethod,
        "applicant": business.Applicant,
        "admission": business.Admission,
        "student": business.Student,
        "lecturer": business.Lecturer,
        "class": business.CourseClass,
        "class_lecturer": business.ClassLecturer,
        "enrollment": business.Enrollment,
        "enrollment_grade": business.EnrollmentGrade,
        "employee_qualification": business.EmployeeQualification,
        "assignment": business.Assignment,
    }.items()
}


class BusinessSyncBlockedError(ValueError):
    def __init__(self, code: str, entity: str, field: str | None = None) -> None:
        super().__init__(code)
        self.code, self.entity, self.field = code, entity, field

    def report(self) -> dict[str, Any]:
        return {"code": self.code, "entity": self.entity, "field": self.field}


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))


def _save_identity(
    connection: Connection,
    namespace: str,
    entity: str,
    key: str,
    target_key: dict[str, Any],
    job_id: str,
    exists: bool,
) -> None:
    identity_condition = (
        IDENTITIES.c.namespace == namespace,
        IDENTITIES.c.entity == entity,
        IDENTITIES.c.source_key == key,
    )
    record = {
        "namespace": namespace,
        "entity": entity,
        "source_key": key,
        "target_key": target_key,
        "target_fingerprint": hashlib.sha256(canonical(target_key).encode()).hexdigest(),
        "last_gold_job_id": job_id,
        "updated_at": datetime.now().astimezone(),
    }
    if not exists:
        connection.execute(insert(IDENTITIES).values(**record))
    else:
        connection.execute(update(IDENTITIES).where(*identity_condition).values(**record))
