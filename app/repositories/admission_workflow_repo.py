"""Shared transactional helpers for Gold imports and the admissions API."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import Table, select, text, update

from app.modules.admissions.models import EVENTS
from app.modules.admissions.policy import (
    READ_ROLES,
    STAFF_ROLES,
    AdmissionActor,
    AdmissionRuleError,
)
from app.modules.ingestion.business_imports import fingerprint, packed
from app.modules.users.models import Admission
from app.repositories.business_identity import IDENTITIES, canonical

TABLE = cast("Table", Admission.__table__)


def actor_roles(connection: Any, user_id: int, *, lock: bool = True) -> frozenset[str]:
    active = connection.execute(
        text(
            "SELECT app_user_id FROM app.app_user WHERE app_user_id=:id AND is_active"
            + (" FOR SHARE" if lock else "")
        ),
        {"id": user_id},
    ).first()
    if active is None:
        raise AdmissionRuleError("ADMISSION_ACTOR_FORBIDDEN")
    return frozenset(
        connection.execute(
            text(
                """
        SELECT r.role_code FROM app.user_role ur JOIN app.role r ON r.role_id=ur.role_id
        WHERE ur.app_user_id=:id
    """
                + (" FOR SHARE OF ur" if lock else "")
            ),
            {"id": user_id},
        ).scalars()
    )


def dataset_scope(
    connection: Any, dataset_id: int, roles: frozenset[str], *, lock: bool = True
) -> dict[str, Any]:
    row = (
        connection.execute(
            text(
                """
        SELECT d.dataset_id,d.source_system_id,dep.department_code,dep.status AS department_status
        FROM app.dataset d JOIN app.source_system s USING(source_system_id)
        LEFT JOIN app.department dep ON dep.department_id=d.owner_department_id
        WHERE d.dataset_id=:id AND d.domain='admissions' AND d.status='ACTIVE' AND s.is_active
    """
                + (" FOR SHARE OF d,s" if lock else "")
            ),
            {"id": dataset_id},
        )
        .mappings()
        .first()
    )
    if row is None or (
        roles.isdisjoint({"ADMIN", "DATA_ADMIN"})
        and (row["department_code"] != "PTS" or row["department_status"] != "ACTIVE")
    ):
        raise AdmissionRuleError("ADMISSION_DATASET_FORBIDDEN")
    return dict(row)


def api_actor(connection: Any, user: dict[str, Any], dataset_id: int) -> AdmissionActor:
    try:
        user_id = int(user["sub"])
    except (TypeError, ValueError, KeyError):
        raise AdmissionRuleError("ADMISSION_ACTOR_FORBIDDEN") from None
    roles = actor_roles(connection, user_id).intersection(user.get("roles", []))
    if roles.isdisjoint(READ_ROLES):
        raise AdmissionRuleError("ADMISSION_ACTOR_FORBIDDEN")
    dataset = dataset_scope(connection, dataset_id, roles)
    return AdmissionActor(user_id, roles, dataset_id, str(dataset["source_system_id"]), "")


def load_import_actor(connection: Any, job: dict[str, Any], *, lock: bool = True) -> AdmissionActor:
    row = (
        connection.execute(
            text("""
        SELECT ij.ingestion_job_id,ij.requested_by,ij.upload_input,d.source_system_id
        FROM app.cleaning_run c JOIN app.ingestion_job ij ON ij.ingestion_job_id=c.ingestion_job_id
        JOIN app.dataset d ON d.dataset_id=ij.dataset_id
        WHERE c.silver_snapshot_id=:silver_snapshot_id AND ij.dataset_id=:dataset_id
            AND c.publication_status IN ('PUBLISHED','PUBLISHED_WITH_QUARANTINE')
        ORDER BY c.finished_at DESC NULLS LAST LIMIT 1
    """),
            job,
        )
        .mappings()
        .first()
    )
    if row is None or not str(row["requested_by"]).isdecimal():
        raise AdmissionRuleError("ADMISSION_ACTOR_FORBIDDEN")
    user_id = int(row["requested_by"])
    roles = actor_roles(connection, user_id, lock=lock)
    if roles.isdisjoint(STAFF_ROLES):
        raise AdmissionRuleError("ADMISSION_ACTOR_FORBIDDEN")
    dataset_scope(connection, job["dataset_id"], roles, lock=lock)
    source = row["upload_input"] or {}
    request_key = fingerprint(
        {
            "dataset": job["dataset_id"],
            "namespace": str(row["source_system_id"]),
            "sha256": source.get("sha256"),
            "parser": source.get("parser_options"),
            "options": source.get("business_import"),
        }
    )
    if not source.get("sha256"):
        raise AdmissionRuleError("ADMISSION_FILE_EVIDENCE_REQUIRED")
    return AdmissionActor(
        user_id, roles, job["dataset_id"], str(row["source_system_id"]), request_key
    )


def source_target(connection: Any, namespace: str, row: dict[str, Any]) -> dict[str, Any]:
    key = connection.execute(
        select(IDENTITIES.c.target_key).where(
            IDENTITIES.c.namespace == namespace,
            IDENTITIES.c.entity == "applicant",
            IDENTITIES.c.source_key == canonical([row["applicant_code"]]),
        )
    ).scalar_one_or_none()
    if key is None:
        raise AdmissionRuleError("ADMISSION_APPLICANT_MAPPING_MISSING", "applicant_code")
    target = (
        connection.execute(
            text("""
        SELECT a.* FROM app.admission a JOIN app.program p USING(program_id)
        JOIN app.admission_year y USING(admission_year_id)
        JOIN app.admission_method m USING(admission_method_id)
        WHERE a.applicant_id=:applicant_id AND p.program_code=:program_code
            AND y.year_label=:year_label AND m.method_code=:method_code
        FOR UPDATE OF a
    """),
            {**row, "applicant_id": key["applicant_id"]},
        )
        .mappings()
        .first()
    )
    if target is None:
        raise AdmissionRuleError("ADMISSION_REGISTRATION_MISSING", "applicant_code")
    return dict(target)


def scoped_target(
    connection: Any, actor: AdmissionActor, admission_id: int, *, lock: bool = True
) -> dict[str, Any]:
    identity = IDENTITIES.c.target_key["applicant_id"].as_integer()
    query = (
        select(TABLE)
        .where(TABLE.c.admission_id == admission_id)
        .where(
            select(IDENTITIES.c.source_key)
            .where(
                IDENTITIES.c.namespace == actor.namespace,
                IDENTITIES.c.entity == "applicant",
                identity == TABLE.c.applicant_id,
            )
            .exists()
        )
    )
    if lock:
        query = query.with_for_update(of=TABLE)
    row = connection.execute(query).mappings().first()
    if row is None:
        raise AdmissionRuleError("ADMISSION_NOT_FOUND")
    return dict(row)


def method_max_score(connection: Any, method_id: int) -> Any:
    return connection.execute(
        text("SELECT max_score FROM app.admission_method WHERE admission_method_id=:id FOR SHARE"),
        {"id": method_id},
    ).scalar_one()


def check_revision(
    connection: Any, current: dict[str, Any], expected: int | None, request_key: str | None = None
) -> bool:
    if isinstance(expected, bool) or not isinstance(expected, int) or not 0 < expected < 2**63:
        raise AdmissionRuleError("ADMISSION_EXPECTED_REVISION_REQUIRED", "expected_revision")
    if current["revision"] == expected:
        return False
    # Exact file replays are allowed only while their audited result is still current.
    if request_key and connection.dialect.name == "postgresql":
        replay = connection.execute(
            select(EVENTS.c.event_id).where(
                EVENTS.c.admission_id == current["admission_id"],
                EVENTS.c.request_key == request_key,
                EVENTS.c.old_revision == expected,
                EVENTS.c.new_revision == current["revision"],
            )
        ).first()
        if replay is not None:
            return True
    raise AdmissionRuleError(
        "ADMISSION_REVISION_CONFLICT",
        "expected_revision",
        {"current_revision": current["revision"], "expected_revision": expected},
    )


def set_context(connection: Any, evidence: dict[str, Any]) -> None:
    if connection.dialect.name == "postgresql":
        connection.execute(
            text("SELECT set_config('unilake.admission_context',:evidence,true)"),
            {"evidence": packed(evidence)},
        )


def write_changes(
    connection: Any, current: dict[str, Any], changes: dict[str, Any], evidence: dict[str, Any]
) -> dict[str, Any]:
    if not changes:
        return current
    set_context(connection, {**evidence, "admission_id": current["admission_id"]})
    row = (
        connection.execute(
            update(TABLE)
            .where(
                TABLE.c.admission_id == current["admission_id"],
                TABLE.c.revision == current["revision"],
            )
            .values(**changes, revision=current["revision"] + 1)
            .returning(*TABLE.c)
        )
        .mappings()
        .first()
    )
    set_context(connection, {})
    if row is None:
        raise AdmissionRuleError("ADMISSION_REVISION_CONFLICT", "expected_revision")
    return dict(row)


def evidence_for(
    actor: AdmissionActor | None,
    *,
    operation: str,
    job_id: str | None = None,
    request_key: str | None = None,
    source: Any = None,
    reason: str | None = None,
    correction_id: str | None = None,
) -> dict[str, Any]:
    return {
        "actor_id": actor.user_id if actor else None,
        "dataset_id": actor.dataset_id if actor else None,
        "gold_job_id": job_id,
        "request_key": request_key,
        "operation": operation,
        "source": source or {},
        "reason": reason,
        "correction_id": correction_id,
    }
