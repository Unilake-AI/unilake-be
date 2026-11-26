"""Preflight a complete business file, then execute its dependency plan atomically."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from itertools import islice
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    String,
    Table,
    UniqueConstraint,
    and_,
    func,
    insert,
    or_,
    select,
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.infra.db.base import Base
from app.modules.admissions.policy import (
    STAFF_ROLES,
    AdmissionActor,
    AdmissionRuleError,
    enrollment_changes,
    result_changes,
)
from app.modules.ingestion.business_imports import (
    ACADEMIC_RECORD_IMPORTS,
    CATALOG_IMPORTS,
    CONTRACTS,
    ImportContract,
    ImportOptions,
    ImportValidationError,
    contract_for,
    fingerprint,
    issue,
    normalize_row,
    packed,
    supported_contract_fingerprints,
)
from app.repositories.business_identity import (
    IDENTITIES,
    MODELS,
    BusinessSyncBlockedError,
    _save_identity,
    canonical,
)

if TYPE_CHECKING:
    from typing import BinaryIO

IMPORT_PLAN_VERSION = 1

PREPARATION_TYPES = {
    "hr_employee": (),
    "hr_position_catalog": (),
    "hr_qualification_catalog": (("qualification", "qualification_name", "qualification_name"),),
    "admissions_year_catalog": (),
    "admissions_method_catalog": (),
    "admissions_registration": (
        ("program", "program_code", "program_code"),
        ("admission_year", "year_label", "year_label"),
        ("admission_method", "method_code", "method_code"),
    ),
    "academic_curriculum": (
        ("program", "program_code", "program_code"),
        ("course", "course_code", "course_code"),
        ("course", "course_code", "prerequisite_course_code"),
    ),
    "academic_student": (
        ("program", "program_code", "program_code"),
        ("admission_year", "year_label", "year_label"),
    ),
    "academic_class": (
        ("course", "course_code", "course_code"),
        ("semester", "semester_code", "semester_code"),
    ),
    "academic_enrollment": (
        ("student", "student_code", "student_code"),
        ("class", "class_code", "class_code"),
    ),
    "academic_enrollment_grade": (
        ("student", "student_code", "student_code"),
        ("class", "class_code", "class_code"),
    ),
}
PREPARATION_ENTITIES = {CONTRACTS[name].entity for name in PREPARATION_TYPES}
GRADE_LIMITS = {"midterm_score": 10, "final_score": 10, "total_score": 10, "grade_value": 4}

CHANGES = Table(
    "business_import_change",
    Base.metadata,
    Column("gold_job_id", String(36), primary_key=True),
    Column("entity", String(100), primary_key=True),
    Column("target_fingerprint", String(64), primary_key=True),
    Column("target_key", JSON().with_variant(JSONB(), "postgresql"), nullable=False),
    Column("action", String(12), nullable=False),
    Column("evidence", JSON().with_variant(JSONB(), "postgresql"), nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    schema="app",
)


class ImportBlockedError(BusinessSyncBlockedError):
    def __init__(self, errors: list[dict[str, Any]]) -> None:
        super().__init__(
            errors[0]["code"], errors[0].get("entity", "import"), errors[0].get("field")
        )
        self.errors = errors

    def report(self) -> dict[str, Any]:
        return {**super().report(), "issues": self.errors[:200], "issue_count": len(self.errors)}


@dataclass(frozen=True)
class Ref:
    operation: str
    field: str


@dataclass
class Operation:
    token: str
    entity: str
    source_key: str
    values: dict[str, Any]
    target: dict[str, Any] | None
    origins: list[dict[str, Any]] = field(default_factory=list)
    natural: tuple[str, ...] = ()
    workflow: str | None = None
    expected_revision: int | None = None
    request_key: str | None = None


class ImportResult(dict[str, int]):
    def __init__(self, tables: dict[str, dict[str, int]], input_rows: int) -> None:
        super().__init__((entity, sum(counts.values())) for entity, counts in tables.items())
        self.report = {"tables": tables, "input_rows": input_rows, "atomic": True}


class Planner:
    def __init__(
        self,
        connection: Any,
        namespace: str,
        options: ImportOptions,
        mapping: dict[str, str],
        actor: AdmissionActor | None = None,
        *,
        lock_rows: bool = True,
    ) -> None:
        self.connection, self.namespace, self.options, self.mapping = (
            connection,
            namespace,
            options,
            mapping,
        )
        self.operations: dict[str, Operation] = {}
        self.errors: list[dict[str, Any]] = []
        self.origin: dict[str, Any] = {}
        self.lookup_cache: dict[str, Any] = {}
        self.row_checks: dict[str, Any] = {}
        self.inactive_admission_methods: set[int] = set()
        self.actor = actor
        self.lock_rows = lock_rows

    def lock(self, query: Any, *, shared: bool = False) -> Any:
        return query.with_for_update(read=shared) if self.lock_rows else query

    def preload_catalogs(self, contract: ImportContract, rows: list[dict[str, Any]]) -> None:
        codes: dict[tuple[str, str], set[str]] = defaultdict(set)
        for entity, column, field_name in PREPARATION_TYPES.get(contract.name, ()):
            codes[entity, column].update(row[field_name] for row in rows if row.get(field_name))
        for (entity, column), distinct in sorted(codes.items()):
            table = MODELS[entity]
            remaining = iter(sorted(distinct))
            while chunk := list(islice(remaining, 500)):
                query = select(table).where(table.c[column].in_(chunk)).order_by(*table.primary_key)
                for value in chunk:
                    self.lookup_cache[canonical([entity, {column: value}])] = []
                for target in self.connection.execute(self.lock(query, shared=True)).mappings():
                    key = canonical([entity, {column: target[column]}])
                    self.lookup_cache[key].append(target)
        if contract.name == "academic_enrollment_grade":
            self.preload_grade_enrollments(rows)

    def preload_grade_enrollments(self, rows: list[dict[str, Any]]) -> None:
        fields = ("student_id", "class_id")
        keys = set()
        for row in rows:
            students = self.lookup_cache[
                canonical(["student", {"student_code": row["student_code"]}])
            ]
            classes = self.lookup_cache[canonical(["class", {"class_code": row["class_code"]}])]
            if len(students) == len(classes) == 1:
                keys.add((students[0]["student_id"], classes[0]["class_id"]))
        matches = self.matching_rows(MODELS["enrollment"], fields, keys, shared=True)
        for key in keys:
            token = canonical(["enrollment", dict(zip(fields, key, strict=True))])
            self.lookup_cache[token] = [matches[key]] if key in matches else []

    def matching_rows(
        self,
        table: Any,
        fields: tuple[str, ...],
        keys: set[tuple[Any, ...]],
        *conditions: Any,
        lock: bool = True,
        shared: bool = False,
    ) -> dict[tuple[Any, ...], Any]:
        columns = [table.c[name] for name in fields]
        result = {}
        remaining = iter(sorted(keys))
        while batch := list(islice(remaining, 500)):
            match = (
                columns[0].in_([key[0] for key in batch])
                if len(columns) == 1
                else tuple_(*columns).in_(batch)
            )
            query = select(table).where(*conditions, match).order_by(*table.primary_key)
            for row in self.connection.execute(
                self.lock(query, shared=shared) if lock else query
            ).mappings():
                result[tuple(row[name] for name in fields)] = row
        return result

    def resolve_existing(self, contract: ImportContract) -> None:
        by_entity: dict[str, list[Operation]] = defaultdict(list)
        for op in self.operations.values():
            by_entity[op.entity].append(op)
        for entity in contract.writes:
            operations = by_entity[entity]
            if not operations:
                continue
            table = MODELS[entity]
            pk = tuple(col.name for col in table.primary_key)
            identities = self.matching_rows(
                IDENTITIES,
                ("source_key",),
                {(op.source_key,) for op in operations},
                IDENTITIES.c.namespace == self.namespace,
                IDENTITIES.c.entity == entity,
                lock=False,
            )
            targets = self.matching_rows(
                table,
                pk,
                {tuple(row["target_key"][name] for name in pk) for row in identities.values()},
            )
            by_natural: dict[tuple[str, ...], dict[tuple[Any, ...], list[Operation]]] = defaultdict(
                lambda: defaultdict(list)
            )
            for op in operations:
                op.values = {
                    name: self.resolved(value) if isinstance(value, Ref) else value
                    for name, value in op.values.items()
                }
                identity = identities.get((op.source_key,))
                if identity is not None:
                    key = tuple(identity["target_key"][name] for name in pk)
                    op.target = targets.get(key)
                    if op.target is None:
                        self.origin = op.origins[0]
                        self.error("IMPORT_TARGET_MISSING", entity)
                elif op.natural:
                    natural_key = tuple(op.values.get(name) for name in op.natural)
                    if all(v is not None and not isinstance(v, Ref) for v in natural_key):
                        by_natural[op.natural][natural_key].append(op)
            for fields, matches in by_natural.items():
                existing = self.matching_rows(table, fields, set(matches))
                for key, pending in matches.items():
                    for op in pending:
                        op.target = existing.get(key)

    def error(self, code: str, entity: str, name: str | None = None, **extra: Any) -> None:
        self.errors.append(
            issue(
                code,
                name,
                entity=entity,
                column=self.mapping.get(name) if name is not None else None,
                **self.origin,
                **extra,
            )
        )

    def lookup_row(
        self,
        entity: str,
        filters: dict[str, Any],
        source_field: str,
        *,
        missing_code: str = "IMPORT_REFERENCE_MISSING",
    ) -> Any:
        token = canonical([entity, filters])
        table = MODELS[entity]
        if token not in self.lookup_cache:
            self.lookup_cache[token] = (
                self.connection.execute(
                    self.lock(
                        select(table).where(*(table.c[k] == v for k, v in filters.items())),
                        shared=True,
                    )
                )
                .mappings()
                .all()
            )
        targets = self.lookup_cache[token]
        if len(targets) != 1:
            self.error(
                missing_code if not targets else "IMPORT_REFERENCE_AMBIGUOUS",
                entity,
                source_field,
            )
            return None
        return targets[0]

    def lookup(self, entity: str, filters: dict[str, Any], source_field: str) -> Any:
        target = self.lookup_row(entity, filters, source_field)
        return target[next(iter(MODELS[entity].primary_key)).name] if target is not None else None

    def operation(
        self, entity: str, key: Any, values: dict[str, Any], natural: tuple[str, ...] = ()
    ) -> Ref:
        table = MODELS[entity]
        source_key = canonical(key)
        if len(source_key) > 1024:
            self.error("IMPORT_SOURCE_KEY_TOO_LONG", entity)
        token = canonical([entity, key])
        first_pk = next(iter(table.primary_key)).name
        if token in self.operations:
            op = self.operations[token]
            for name, value in values.items():
                if value is None:
                    continue
                if op.values.get(name) is not None and op.values[name] != value:
                    self.error(
                        "IMPORT_CONFLICTING_ROWS",
                        entity,
                        name,
                        previous_locators=[o["locator"] for o in op.origins],
                    )
                else:
                    op.values[name] = value
            op.origins.append(dict(self.origin))
            return Ref(token, first_pk)
        if self.options.import_type in PREPARATION_TYPES:
            self.operations[token] = Operation(
                token, entity, source_key, values, None, [dict(self.origin)], natural
            )
            return Ref(token, first_pk)
        identity = (
            self.connection.execute(
                select(IDENTITIES).where(
                    IDENTITIES.c.namespace == self.namespace,
                    IDENTITIES.c.entity == entity,
                    IDENTITIES.c.source_key == source_key,
                )
            )
            .mappings()
            .first()
        )
        target = None
        if identity is not None:
            target = (
                self.connection.execute(
                    self.lock(
                        select(table).where(
                            *(table.c[k] == v for k, v in identity["target_key"].items())
                        )
                    )
                )
                .mappings()
                .first()
            )
            if target is None:
                self.error("IMPORT_TARGET_MISSING", entity)
        elif natural and all(
            values.get(n) is not None and not isinstance(values[n], Ref) for n in natural
        ):
            target = (
                self.connection.execute(
                    self.lock(select(table).where(*(table.c[n] == values[n] for n in natural)))
                )
                .mappings()
                .first()
            )
        op = Operation(
            token,
            entity,
            source_key,
            dict(values),
            dict(target) if target is not None else None,
            [dict(self.origin)],
        )
        self.operations[token] = op
        return Ref(token, first_pk)

    def resolved(self, ref: Ref) -> Any:
        op = self.operations[ref.operation]
        return op.target[ref.field] if op.target else ref

    def add_row(self, contract: Any, row: dict[str, Any]) -> None:
        if contract.name == "hr_employee":
            flag_key = "lecturer:" + row["employee_code"]
            if flag_key in self.row_checks and self.row_checks[flag_key] != row["is_lecturer"]:
                self.error("IMPORT_CONFLICTING_ROWS", "lecturer", "is_lecturer")
            self.row_checks[flag_key] = row["is_lecturer"]
            values = self.fields("employee", row, exclude={"employee_id"})
            employee = self.operation(
                "employee", {"natural": [row["employee_code"]]}, values, ("employee_code",)
            )
            if row["is_lecturer"]:
                employee_id = self.resolved(employee)
                self.operation(
                    "lecturer",
                    {"employee_code": row["employee_code"]},
                    {
                        **self.fields("lecturer", row),
                        "employee_id": employee_id,
                    },
                    ("employee_id",),
                )
            elif any(
                row.get(n) is not None
                for n in ("academic_degree", "academic_title", "research_field")
            ):
                self.error("IMPORT_LECTURER_FIELDS_WITHOUT_LECTURER", "lecturer", "is_lecturer")
            # Có -> Không deliberately preserves the existing lecturer and its links.
        elif contract.name == "hr_assignment":
            employee = self.lookup(
                "employee", {"employee_code": row["employee_code"]}, "employee_code"
            )
            department_code = row["department_code"]
            if department_code not in {"PDT", "PNS", "PTS"}:
                self.error("IMPORT_SYSTEM_DEPARTMENT_ONLY", "assignment", "department_code")
                department = None
            else:
                department = self.lookup(
                    "department",
                    {"department_code": department_code, "status": "ACTIVE"},
                    "department_code",
                )
            position = self.lookup(
                "position", {"position_code": row["position_code"]}, "position_code"
            )
            self.operation(
                "assignment",
                {"import_assignment": row["assignment_code"]},
                {
                    **self.fields("assignment", row),
                    "employee_id": employee,
                    "department_id": department,
                    "position_id": position,
                },
            )
        elif contract.name == "hr_qualification":
            employee = self.lookup(
                "employee", {"employee_code": row["employee_code"]}, "employee_code"
            )
            filters = {
                n: row[n]
                for n in ("qualification_name", "qualification_type")
                if row.get(n) is not None
            }
            qualification = self.lookup("qualification", filters, "qualification_name")
            self.operation(
                "employee_qualification",
                [employee, qualification],
                {
                    **self.fields("employee_qualification", row),
                    "employee_id": employee,
                    "qualification_id": qualification,
                },
                ("employee_id", "qualification_id"),
            )
        elif contract.name == "admissions_registration":
            program = self.lookup("program", {"program_code": row["program_code"]}, "program_code")
            year = self.lookup("admission_year", {"year_label": row["year_label"]}, "year_label")
            method = self.lookup_row(
                "admission_method", {"method_code": row["method_code"]}, "method_code"
            )
            method_id = method["admission_method_id"] if method is not None else None
            if method is not None and not method["is_active"]:
                self.inactive_admission_methods.add(method["admission_method_id"])
            applicant = self.operation(
                "applicant", [row["applicant_code"]], self.fields("applicant", row)
            )
            applicant_id = self.resolved(applicant)
            reference = self.operation(
                "admission",
                {"registration": [row[n] for n in contract.anchors]},
                {
                    **self.fields("admission", row),
                    "applicant_id": applicant_id,
                    "program_id": program,
                    "admission_year_id": year,
                    "admission_method_id": method_id,
                },
                ("applicant_id", "program_id", "admission_year_id", "admission_method_id"),
            )
            self._workflow(reference, contract.name, row.get("expected_revision"))
        elif contract.name in {"admissions_results", "admissions_enrollment"}:
            from app.repositories.admission_workflow_repo import source_target

            if (
                self.actor is None
                or self.actor.namespace != self.namespace
                or self.actor.roles.isdisjoint(STAFF_ROLES)
            ):
                raise AdmissionRuleError("ADMISSION_ACTOR_FORBIDDEN")
            target = source_target(self.connection, self.namespace, row)
            allowed = (
                ("admission_score", "admission_status")
                if contract.name == "admissions_results"
                else ("enrollment_status", "enrollment_date")
            )
            values = {n: row[n] for n in allowed if row.get(n) is not None}
            reference = self.operation(
                "admission",
                {
                    "registration": [
                        row[n]
                        for n in ("applicant_code", "program_code", "year_label", "method_code")
                    ]
                },
                values,
            )
            self.operations[reference.operation].target = target
            self._workflow(reference, contract.name, row.get("expected_revision"))
        elif contract.name == "academic_curriculum":
            program = self.lookup("program", {"program_code": row["program_code"]}, "program_code")
            course = self.lookup("course", {"course_code": row["course_code"]}, "course_code")
            prerequisite = (
                self.lookup(
                    "course",
                    {"course_code": row["prerequisite_course_code"]},
                    "prerequisite_course_code",
                )
                if row.get("prerequisite_course_code")
                else None
            )
            curriculum = self.operation(
                "curriculum",
                {"natural": [program, row["version"]]},
                {
                    **self.fields("curriculum", row),
                    "program_id": program,
                },
                ("program_id", "version"),
            )
            self.operation(
                "curriculum_course",
                {"curriculum_course": [program, row["version"], course]},
                {
                    **self.fields("curriculum_course", row),
                    "curriculum_id": self.resolved(curriculum),
                    "course_id": course,
                    "prerequisite_course_id": prerequisite,
                },
                ("curriculum_id", "course_id"),
            )
        elif contract.name in ACADEMIC_RECORD_IMPORTS:
            self.add_academic_row(contract, row)
        elif contract.name in CATALOG_IMPORTS:
            self.add_catalog_row(contract, row)

    def add_catalog_row(self, contract: ImportContract, row: dict[str, Any]) -> None:
        entity = contract.writes[0]
        key = [row[field] for field in contract.anchors]
        if entity == "qualification":
            matches = self.lookup_cache[canonical([entity, {"qualification_name": key[0]}])]
            if len(matches) > 1:
                self.error("IMPORT_QUALIFICATION_NAME_AMBIGUOUS", entity, "qualification_name")
                return
        self.operation(entity, {"natural": key}, self.fields(entity, row), contract.anchors)

    def add_academic_row(self, contract: ImportContract, row: dict[str, Any]) -> None:
        entity = contract.writes[0]
        references = {
            target: self.lookup_row(target, {column: row[field]}, field)
            for target, column, field in PREPARATION_TYPES[contract.name]
        }
        if any(target is None for target in references.values()):
            return
        foreign_keys = {f"{name}_id": target[f"{name}_id"] for name, target in references.items()}

        if entity == "student":
            natural = key = ("student_code",)
        elif entity == "class":
            natural = key = ("class_code",)
        else:
            key = contract.anchors
            natural = ("student_id", "class_id")
        if entity == "enrollment_grade":
            enrollment = self.lookup_row(
                "enrollment",
                foreign_keys,
                "class_code",
                missing_code="IMPORT_ACADEMIC_ENROLLMENT_REQUIRED",
            )
            if enrollment is None:
                return
            if not any(row.get(name) is not None for name in (*GRADE_LIMITS, "letter_grade")):
                self.error("IMPORT_GRADE_REQUIRED", entity)
                return
            registration_date = row.get("registration_date")
            if registration_date is not None and registration_date != enrollment["enrollment_date"]:
                self.error("IMPORT_GRADE_REGISTRATION_DATE_MISMATCH", entity, "registration_date")
            natural = ("enrollment_id",)
            foreign_keys = {"enrollment_id": enrollment["enrollment_id"]}
        if entity == "enrollment" and (registered_at := row.get("enrollment_date")):
            semester = self.lookup_row(
                "semester", {"semester_id": references["class"]["semester_id"]}, "class_code"
            )
            if semester is not None and registered_at > semester["end_date"]:
                self.error(
                    "IMPORT_DATE_RANGE_INVALID",
                    entity,
                    "enrollment_date",
                    message="Ngày đăng ký học phần phải trước hoặc bằng ngày kết thúc học kỳ.",
                )
        self.operation(
            entity,
            {"natural": [row[field] for field in key]},
            {**self.fields(entity, row), **foreign_keys},
            natural,
        )

    def _workflow(self, ref: Ref, name: str, expected_revision: int | None) -> None:
        op = self.operations[ref.operation]
        if op.workflow is not None and op.expected_revision != expected_revision:
            self.error("IMPORT_CONFLICTING_ROWS", "admission", "expected_revision")
        op.workflow, op.expected_revision = name, expected_revision

    def _validate_admission(self, op: Operation) -> None:
        from app.repositories.admission_workflow_repo import check_revision, method_max_score

        if not op.workflow or op.target is None:
            return
        requested = {n: v for n, v in op.values.items() if v is not None}
        if (
            op.workflow == "admissions_registration"
            and requested.get("admission_status") is not None
        ):
            if (
                str(requested["admission_status"]).upper()
                != str(op.target["admission_status"]).upper()
            ):
                raise AdmissionRuleError(
                    "IMPORT_STATUS_TRANSITION_POLICY_REQUIRED", "admission_status"
                )
            op.values.pop("admission_status", None)
            requested.pop("admission_status", None)
        changes = {n: v for n, v in requested.items() if v != op.target[n]}
        if op.workflow != "admissions_registration":
            maximum = method_max_score(self.connection, op.target["admission_method_id"])
            changes = (
                result_changes(op.target, requested, maximum)
                if op.workflow == "admissions_results"
                else enrollment_changes(op.target, requested)
            )
        if changes or op.expected_revision is not None:
            if (
                self.actor is None
                or self.actor.namespace != self.namespace
                or self.actor.roles.isdisjoint(STAFF_ROLES)
            ):
                raise AdmissionRuleError("ADMISSION_ACTOR_FORBIDDEN")
            op.request_key = fingerprint(
                {
                    "file": self.actor.request_key,
                    "workflow": op.workflow,
                    "source_key": op.source_key,
                    "values": requested,
                    "expected_revision": op.expected_revision,
                }
            )
            replay = check_revision(
                self.connection, op.target, op.expected_revision, op.request_key
            )
            if replay and changes:
                raise AdmissionRuleError("ADMISSION_REVISION_CONFLICT", "expected_revision")
        if op.workflow != "admissions_registration":
            op.values = changes

    @staticmethod
    def fields(entity: str, row: dict[str, Any], exclude: set[str] | None = None) -> dict[str, Any]:
        table = MODELS[entity]
        return {n: v for n, v in row.items() if n in table.c and n not in (exclude or set())}

    def validate_academic_row(self, op: Operation) -> None:
        if op.entity == "class":
            capacity = op.values.get("max_capacity")
            if capacity is not None and not 1 <= capacity <= 200:
                self.error(
                    "IMPORT_VALUE_OUT_OF_RANGE",
                    op.entity,
                    "max_capacity",
                    message="Sĩ số tối đa phải từ 1 đến 200.",
                )
            if op.target:
                for field, column in (
                    ("course_code", "course_id"),
                    ("semester_code", "semester_id"),
                ):
                    if op.values[column] != op.target[column]:
                        self.error(
                            "IMPORT_CLASS_REFERENCE_CHANGE",
                            op.entity,
                            field,
                            message="Mã lớp học phần đã gắn với học phần hoặc học kỳ khác.",
                        )
        elif op.entity == "enrollment_grade":
            for field, bound in GRADE_LIMITS.items():
                score = op.values.get(field)
                if score is not None and not 0 <= score <= bound:
                    self.error(
                        "IMPORT_VALUE_OUT_OF_RANGE",
                        op.entity,
                        field,
                        message=f"Điểm phải từ 0 đến {bound}.",
                    )
        elif op.entity == "student" and op.target and op.target["admission_id"] is not None:
            for field, column in (
                ("program_code", "program_id"),
                ("year_label", "admission_year_id"),
            ):
                if op.values[column] != op.target[column]:
                    self.error(
                        "IMPORT_STUDENT_REFERENCE_CHANGE",
                        op.entity,
                        field,
                        message="Hồ sơ sinh viên đã có liên kết tuyển sinh với chương trình hoặc khóa khác.",
                    )

    def validate_qualification_catalog(self, op: Operation) -> None:
        new_type = op.values.get("qualification_type")
        if not op.target or not new_type:
            return
        previous_type = op.target["qualification_type"]
        if not previous_type or previous_type.casefold() == new_type.casefold():
            return
        links = MODELS["employee_qualification"]
        if self.connection.execute(
            select(links.c.qualification_id)
            .where(links.c.qualification_id == op.target["qualification_id"])
            .limit(1)
        ).first():
            self.error("IMPORT_QUALIFICATION_TYPE_IN_USE", op.entity, "qualification_type")

    def validate_admission_method(self, op: Operation) -> None:
        maximum = op.values.get("max_score")
        if maximum is None:
            return
        if maximum < 0:
            self.error(
                "IMPORT_VALUE_OUT_OF_RANGE",
                op.entity,
                "max_score",
                message="Điểm tối đa phải từ 0 trở lên.",
            )
            return
        if op.target is None or maximum == op.target["max_score"]:
            return
        previous = op.target["max_score"]
        admissions = MODELS["admission"].c
        score = admissions.admission_score
        if maximum == 0:
            incompatible = score.is_not(None)
        elif previous == 0:
            incompatible = or_(
                score > maximum,
                and_(
                    score.is_(None),
                    func.upper(admissions.admission_status).in_(("ACCEPTED", "REJECTED")),
                ),
            )
        elif maximum < previous:
            incompatible = score > maximum
        else:
            return
        if self.connection.execute(
            select(admissions.admission_id)
            .where(admissions.admission_method_id == op.target["admission_method_id"], incompatible)
            .limit(1)
        ).first():
            self.error("IMPORT_ADMISSION_METHOD_SCORE_IN_USE", op.entity, "max_score")

    def validate_open_admission_years(self) -> None:
        years = MODELS["admission_year"].c
        updated_ids = {
            op.target["admission_year_id"] for op in self.operations.values() if op.target
        }
        windows = []
        for op in self.operations.values():
            effective = {
                **(op.target or {}),
                **{k: v for k, v in op.values.items() if v is not None},
            }
            start, end = effective.get("start_date"), effective.get("end_date")
            if str(effective.get("status")).upper() == "OPEN" and start and end and start < end:
                windows.append((op, start, end))
        if not windows:
            return
        existing = [
            (start, end)
            for year_id, start, end in self.connection.execute(
                select(years.admission_year_id, years.start_date, years.end_date).where(
                    func.upper(years.status) == "OPEN"
                )
            )
            if year_id not in updated_ids
        ]
        for op, start, end in windows:
            if any(
                start <= other_end and other_start <= end for other_start, other_end in existing
            ) or any(
                op is not other and start <= other_end and other_start <= end
                for other, other_start, other_end in windows
            ):
                self.origin = op.origins[0]
                self.error("IMPORT_ADMISSION_YEARS_OVERLAP", op.entity, "year_label")

    def validate(self) -> None:
        unique_values: dict[str, Operation] = {}
        for op in self.operations.values():
            self.origin = op.origins[0]
            table = MODELS[op.entity]
            if self.options.import_type in ACADEMIC_RECORD_IMPORTS:
                self.validate_academic_row(op)
            if self.options.import_type == "hr_qualification_catalog":
                self.validate_qualification_catalog(op)
            if self.options.import_type == "admissions_method_catalog":
                self.validate_admission_method(op)
            if op.entity == "admission":
                try:
                    self._validate_admission(op)
                except AdmissionRuleError as exc:
                    self.error(exc.code, "admission", exc.field, message=exc.message, **exc.details)
            if op.target:
                target_key = {c.name: op.target[c.name] for c in table.primary_key}
                owner = self.connection.execute(
                    select(IDENTITIES.c.namespace).where(
                        IDENTITIES.c.entity == op.entity,
                        IDENTITIES.c.target_fingerprint == fingerprint(target_key),
                        IDENTITIES.c.namespace != self.namespace,
                    )
                ).first()
                if owner:
                    self.error("GOLD_SYNC_SOURCE_CONFLICT", op.entity)
            for column in table.c:
                name = column.name
                value = op.values.get(name)
                if value is None:
                    if (
                        op.target is None
                        and not column.nullable
                        and column.autoincrement is not True
                        and not column.server_default
                        and not column.default
                    ):
                        self.error("IMPORT_REQUIRED_FIELD", op.entity, name)
                    continue
                if isinstance(value, Ref):
                    continue
                kind = column.type.python_type
                maximum_length = getattr(column.type, "length", None)
                valid = (
                    (
                        kind is str
                        and isinstance(value, str)
                        and bool(value.strip())
                        and (not maximum_length or len(value) <= maximum_length)
                    )
                    or (kind is int and isinstance(value, int) and not isinstance(value, bool))
                    or (kind is bool and isinstance(value, bool))
                    or (
                        kind is date and isinstance(value, date) and not isinstance(value, datetime)
                    )
                    or (
                        kind is Decimal
                        and isinstance(value, (int, Decimal))
                        and not isinstance(value, bool)
                    )
                )
                if not valid:
                    self.error("IMPORT_VALUE_INVALID", op.entity, name)
                if isinstance(value, Decimal):
                    scale, precision = (
                        getattr(column.type, "scale", 0),
                        getattr(column.type, "precision", 0),
                    )
                    if value.copy_abs() >= Decimal(10) ** (
                        precision - scale
                    ) or value != value.quantize(Decimal(1).scaleb(-scale)):
                        self.error("IMPORT_DECIMAL_OUT_OF_RANGE", op.entity, name)
            for constraint in table.constraints:
                if not isinstance(constraint, UniqueConstraint):
                    continue
                names = [c.name for c in constraint.columns]
                vals = {n: op.values.get(n, (op.target or {}).get(n)) for n in names}
                if not all(v is not None and not isinstance(v, Ref) for v in vals.values()):
                    continue
                unique_key = canonical([op.entity, vals])
                if unique_key in unique_values and unique_values[unique_key].token != op.token:
                    self.error("IMPORT_UNIQUE_CONFLICT", op.entity, names[0])
                unique_values[unique_key] = op
                target = (
                    self.connection.execute(
                        self.lock(select(table).where(*(table.c[n] == v for n, v in vals.items())))
                    )
                    .mappings()
                    .first()
                )
                if target is not None and (
                    op.target is None
                    or any(target[c.name] != op.target[c.name] for c in table.primary_key)
                ):
                    self.error("IMPORT_UNIQUE_CONFLICT", op.entity, names[0])
            if (
                op.entity == "employee_qualification"
                and op.target
                and any(
                    op.values.get(n) is not None and op.values[n] != op.target[n]
                    for n in ("institution", "year_obtained")
                )
            ):
                self.error("IMPORT_QUALIFICATION_INSTANCE_POLICY_REQUIRED", op.entity)
            if (
                op.workflow == "admissions_registration"
                and op.target
                and op.values.get("admission_status") not in {None, op.target["admission_status"]}
            ):
                self.error(
                    "IMPORT_STATUS_TRANSITION_POLICY_REQUIRED", op.entity, "admission_status"
                )
            if op.workflow == "admissions_registration" and op.target is None:
                if op.values.get("admission_status") not in {None, "APPLIED"}:
                    self.error("IMPORT_RESULT_POLICY_REQUIRED", op.entity, "admission_status")
                if op.values.get("admission_method_id") in self.inactive_admission_methods:
                    self.error("IMPORT_ADMISSION_METHOD_INACTIVE", op.entity, "method_code")
            for n in ("preference_rank", "semester_no"):
                if op.values.get(n) is not None and op.values[n] < 1:
                    self.error("IMPORT_POSITIVE_INTEGER_REQUIRED", op.entity, n)
            for start, end in (("start_date", "end_date"), ("effective_from", "effective_to")):
                left = op.values.get(start) or (op.target or {}).get(start)
                right = op.values.get(end) or (op.target or {}).get(end)
                if (
                    left
                    and right
                    and (left > right or (op.entity == "admission_year" and left == right))
                ):
                    self.error("IMPORT_DATE_RANGE_INVALID", op.entity, end)
        if self.options.import_type == "admissions_year_catalog":
            self.validate_open_admission_years()
        if self.errors:
            raise ImportBlockedError(self.errors)

    def execute(self, job_id: str, input_rows: int) -> ImportResult:
        targets: dict[str, dict[str, Any]] = {}
        tables: dict[str, dict[str, int]] = {}
        admission_results: list[dict[str, int]] = []
        for op in self.operations.values():
            table = MODELS[op.entity]
            values = {
                n: (targets[v.operation][v.field] if isinstance(v, Ref) else v)
                for n, v in op.values.items()
                if v is not None
            }
            counts = tables.setdefault(op.entity, {"inserted": 0, "updated": 0, "unchanged": 0})
            changed = (
                values
                if op.target is None
                else {n: v for n, v in values.items() if v != op.target[n]}
            )
            if op.target is None:
                if op.entity == "admission":
                    from app.repositories.admission_workflow_repo import evidence_for, set_context

                    set_context(
                        self.connection,
                        evidence_for(
                            self.actor,
                            operation="REGISTRATION",
                            job_id=job_id,
                            source={"namespace": self.namespace, "origins": op.origins},
                        ),
                    )
                pk = list(table.primary_key)
                if (
                    self.connection.dialect.name == "sqlite"
                    and len(pk) == 1
                    and not pk[0].foreign_keys
                ):
                    values[pk[0].name] = (
                        self.connection.execute(select(func.max(pk[0]))).scalar_one() or 0
                    ) + 1
                target = dict(
                    self.connection.execute(
                        insert(table).values(**values).returning(*table.primary_key)
                    )
                    .mappings()
                    .one()
                )
                action = "inserted"
                if op.entity == "admission":
                    set_context(self.connection, {})
            else:
                target = {c.name: op.target[c.name] for c in table.primary_key}
                action = "updated" if changed else "unchanged"
                if changed:
                    if op.entity == "admission":
                        from app.repositories.admission_workflow_repo import (
                            evidence_for,
                            write_changes,
                        )

                        after = write_changes(
                            self.connection,
                            op.target,
                            changed,
                            evidence_for(
                                self.actor,
                                operation=op.workflow or "REGISTRATION",
                                job_id=job_id,
                                request_key=op.request_key,
                                source={"namespace": self.namespace, "origins": op.origins},
                            ),
                        )
                        if len(admission_results) < 200:
                            admission_results.append(
                                {
                                    "admission_id": after["admission_id"],
                                    "revision": after["revision"],
                                }
                            )
                    else:
                        self.connection.execute(
                            update(table)
                            .where(*(table.c[n] == v for n, v in target.items()))
                            .values(**changed)
                        )
            counts[action] += 1
            targets[op.token] = target
            exists = (
                self.connection.execute(
                    select(IDENTITIES.c.source_key).where(
                        IDENTITIES.c.namespace == self.namespace,
                        IDENTITIES.c.entity == op.entity,
                        IDENTITIES.c.source_key == op.source_key,
                    )
                ).first()
                is not None
            )
            _save_identity(
                self.connection, self.namespace, op.entity, op.source_key, target, job_id, exists
            )
            # Audit and domain changes commit in the worker's same transaction.
            prior = self.connection.execute(
                select(CHANGES.c.action).where(
                    CHANGES.c.gold_job_id == job_id,
                    CHANGES.c.entity == op.entity,
                    CHANGES.c.target_fingerprint == fingerprint(target),
                )
            ).first()
            if prior is None:
                self.connection.execute(
                    insert(CHANGES).values(
                        gold_job_id=job_id,
                        entity=op.entity,
                        target_fingerprint=fingerprint(target),
                        target_key=target,
                        action=action,
                        evidence=json.loads(
                            packed(
                                {
                                    "namespace": self.namespace,
                                    "import_type": self.options.import_type,
                                    "version": self.options.version,
                                    "source_key": op.source_key,
                                    "origins": op.origins,
                                    "fields": list(changed),
                                    "before": {n: op.target[n] for n in changed}
                                    if op.target
                                    else {},
                                    "after": changed,
                                }
                            )
                        ),
                    )
                )
        result = ImportResult(tables, input_rows)
        if admission_results:
            result.report["admissions"] = admission_results
        return result


def plan_import_records(
    connection: Any,
    entity: str,
    records: list[dict[str, Any]],
    *,
    actor: AdmissionActor | None = None,
    lock_rows: bool = True,
) -> Planner:
    expected = next((c for c in CONTRACTS.values() if c.entity == entity), None)
    if expected is None or (not lock_rows and entity not in PREPARATION_ENTITIES):
        raise ImportBlockedError([issue("IMPORT_TYPE_OR_DOMAIN_INVALID")])
    planner = None
    signature = None
    errors: list[dict[str, Any]] = []
    rows: list[tuple[str, dict[str, Any]]] = []
    for record in records:
        try:
            encoded = json.loads(record["_business_import_row"])
            evidence = encoded.get("contract") or record["_business_import_contract"]
            options = ImportOptions.model_validate(evidence["options"])
            contract = contract_for(options, encoded["domain"])
            namespace = encoded["namespace"]
            if not isinstance(namespace, str) or not namespace.isdecimal() or int(namespace) < 1:
                raise ValueError("IMPORT_EVIDENCE_INVALID")
            if (
                contract != expected
                or namespace != str(record["_source_system_id"])
                or evidence["contract_fingerprint"] not in supported_contract_fingerprints(contract)
            ):
                raise ValueError("IMPORT_EVIDENCE_INVALID")
            identity = fingerprint({"contract": evidence, "namespace": namespace})
            if signature is not None and signature != identity:
                raise ValueError("IMPORT_EVIDENCE_INVALID")
            signature = identity
            if planner is None:
                planner = Planner(
                    connection,
                    namespace,
                    options,
                    evidence["field_mapping"],
                    actor,
                    lock_rows=lock_rows,
                )
            values = encoded["values"]
            if set(values) - {f.name for f in contract.fields}:
                raise ValueError("IMPORT_EVIDENCE_INVALID")
            # Values were normalized in Silver; dates in the JSON envelope use ISO.
            normalized = normalize_row(
                contract,
                values,
                {n: n for n in values},
                options.model_copy(update={"date_format": "%Y-%m-%d"}),
                encoded["locator"],
            )
            rows.append((encoded["locator"], normalized))
        except ImportValidationError as exc:
            errors.extend(exc.issues)
        except (KeyError, TypeError, ValueError):
            errors.append(issue("IMPORT_EVIDENCE_INVALID"))
    if planner is None:
        raise ImportBlockedError(errors or [issue("IMPORT_EVIDENCE_INVALID")])
    planner.preload_catalogs(expected, [row for _, row in rows])
    planner.errors.extend(errors)
    for locator, row in rows:
        planner.origin = {"locator": locator, "columns": planner.mapping}
        try:
            planner.add_row(expected, row)
        except AdmissionRuleError as exc:
            planner.error(exc.code, "admission", exc.field, message=exc.message, **exc.details)
    if expected.name in PREPARATION_TYPES:
        planner.resolve_existing(expected)
    planner.validate()
    return planner


def describe_import_plan(
    planner: Planner, input_rows: int, archive: BinaryIO | None = None
) -> dict[str, Any]:
    tables: dict[str, dict[str, int]] = {}
    sample: list[dict[str, Any]] = []
    digest = hashlib.sha256()
    operation_ids = {token: i for i, token in enumerate(planner.operations, 1)}
    header = {
        "format_version": IMPORT_PLAN_VERSION,
        "import_type": planner.options.import_type,
        "contract_version": planner.options.version,
        "context": planner.options.context,
        "input_rows": input_rows,
    }

    def append(record: dict[str, Any]) -> None:
        line = (packed(record) + "\n").encode()
        digest.update(line)
        if archive is not None:
            archive.write(line)

    append({**header, "options": planner.options.metadata(), "namespace": planner.namespace})
    for op in planner.operations.values():
        table = MODELS[op.entity]
        values = {
            name: {"operation_id": operation_ids[value.operation], "field": value.field}
            if isinstance(value, Ref)
            else value
            for name, value in op.values.items()
            if value is not None
        }
        changed = [
            name for name, value in values.items() if op.target is None or op.target[name] != value
        ]
        action = "inserted" if op.target is None else "updated" if changed else "unchanged"
        counts = tables.setdefault(op.entity, {"inserted": 0, "updated": 0, "unchanged": 0})
        counts[action] += 1
        locators = [item["locator"] for item in op.origins]
        entry = {
            "operation_id": operation_ids[op.token],
            "table": op.entity,
            "action": action,
            "fields": sorted(changed),
            "source_row_count": len(locators),
        }
        if len(sample) < 100:
            sample.append({**entry, "source_rows": locators[:20]})
        append(
            {
                **entry,
                "source_key": op.source_key,
                "source_rows": locators,
                "target_key": {c.name: op.target[c.name] for c in table.primary_key}
                if op.target is not None
                else None,
                "target_fingerprint": fingerprint(dict(op.target))
                if op.target is not None
                else None,
                "values_fingerprint": fingerprint(values),
                "references": {
                    name: value for name, value in values.items() if table.c[name].foreign_keys
                },
            }
        )
    return {
        **header,
        "tables": tables,
        "operation_count": len(operation_ids),
        "operations": sample,
        "plan_fingerprint": digest.hexdigest(),
    }


def sync_import_records(
    connection: Any,
    entity: str,
    records: list[dict[str, Any]],
    job_id: str,
    *,
    actor: AdmissionActor | None = None,
    expected_plan: str | None = None,
) -> ImportResult:
    if not records:
        return ImportResult({}, 0)
    planner = plan_import_records(connection, entity, records, actor=actor)
    if expected_plan is not None:
        observed = describe_import_plan(planner, len(records))["plan_fingerprint"]
        if observed != expected_plan:
            raise ImportBlockedError([issue("IMPORT_PLAN_STALE")])
    return planner.execute(job_id, len(records))
