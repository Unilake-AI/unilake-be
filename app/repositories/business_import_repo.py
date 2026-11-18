"""Preflight a complete business file, then execute its dependency plan atomically."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    String,
    Table,
    UniqueConstraint,
    func,
    insert,
    select,
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
    CONTRACTS,
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
        self.actor = actor

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

    def lookup(self, entity: str, filters: dict[str, Any], source_field: str) -> Any:
        token = canonical([entity, filters])
        table = MODELS[entity]
        if token not in self.lookup_cache:
            self.lookup_cache[token] = (
                self.connection.execute(
                    select(table)
                    .where(*(table.c[k] == v for k, v in filters.items()))
                    .with_for_update(read=True)
                )
                .mappings()
                .all()
            )
        targets = self.lookup_cache[token]
        if len(targets) != 1:
            self.error(
                "IMPORT_REFERENCE_MISSING" if not targets else "IMPORT_REFERENCE_AMBIGUOUS",
                entity,
                source_field,
            )
            return None
        return targets[0][next(iter(table.primary_key)).name]

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
                    select(table)
                    .where(*(table.c[k] == v for k, v in identity["target_key"].items()))
                    .with_for_update()
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
                    select(table)
                    .where(*(table.c[n] == values[n] for n in natural))
                    .with_for_update()
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
            method = self.lookup(
                "admission_method", {"method_code": row["method_code"]}, "method_code"
            )
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
                    "admission_method_id": method,
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

    def validate(self) -> None:
        unique_values: dict[str, Operation] = {}
        for op in self.operations.values():
            self.origin = op.origins[0]
            table = MODELS[op.entity]
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
                    if value != value.quantize(Decimal(1).scaleb(-scale)) or abs(value) >= Decimal(
                        10
                    ) ** (precision - scale):
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
                        select(table)
                        .where(*(table.c[n] == v for n, v in vals.items()))
                        .with_for_update()
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
            if (
                op.workflow == "admissions_registration"
                and op.target is None
                and op.values.get("admission_status") not in {None, "APPLIED"}
            ):
                self.error("IMPORT_RESULT_POLICY_REQUIRED", op.entity, "admission_status")
            for n in ("preference_rank", "semester_no"):
                if op.values.get(n) is not None and op.values[n] < 1:
                    self.error("IMPORT_POSITIVE_INTEGER_REQUIRED", op.entity, n)
            for start, end in (("start_date", "end_date"), ("effective_from", "effective_to")):
                left = op.values.get(start) or (op.target or {}).get(start)
                right = op.values.get(end) or (op.target or {}).get(end)
                if left and right and left > right:
                    self.error("IMPORT_DATE_RANGE_INVALID", op.entity, end)
        if self.errors:
            raise ImportBlockedError(self.errors)

    def execute(self, job_id: str, input_rows: int) -> ImportResult:
        self.validate()
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


def sync_import_records(
    connection: Any,
    entity: str,
    records: list[dict[str, Any]],
    job_id: str,
    *,
    actor: AdmissionActor | None = None,
) -> ImportResult:
    if not records:
        return ImportResult({}, 0)
    expected = next((c for c in CONTRACTS.values() if c.entity == entity), None)
    if expected is None:
        raise ImportBlockedError([issue("IMPORT_TYPE_OR_DOMAIN_INVALID")])
    planner = None
    signature = None
    errors = []
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
                planner = Planner(connection, namespace, options, evidence["field_mapping"], actor)
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
            for n in normalized:
                if (n == "status" or n.endswith("_status")) and isinstance(normalized[n], str):
                    normalized[n] = normalized[n].upper()
            planner.origin = {"locator": encoded["locator"], "columns": evidence["field_mapping"]}
            planner.add_row(contract, normalized)
        except AdmissionRuleError as exc:
            errors.append(
                issue(
                    exc.code,
                    exc.field,
                    entity="admission",
                    locator=encoded.get("locator"),
                    column=evidence.get("field_mapping", {}).get(exc.field),
                    message=exc.message,
                    **exc.details,
                )
            )
        except ImportValidationError as exc:
            errors.extend(exc.issues)
        except (KeyError, TypeError, ValueError):
            errors.append(issue("IMPORT_EVIDENCE_INVALID"))
    if errors:
        raise ImportBlockedError(errors)
    assert planner is not None
    return planner.execute(job_id, len(records))
