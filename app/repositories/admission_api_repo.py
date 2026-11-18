"""Read source-scoped registrations and execute explicit correction requests."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, insert, select, text, update

from app.modules.admissions.models import CORRECTIONS, EVENTS
from app.modules.admissions.policy import (
    CORRECTION_ROLES,
    AdmissionActor,
    AdmissionRuleError,
    enrollment_changes,
    result_changes,
)
from app.modules.ingestion.business_imports import packed
from app.repositories.admission_workflow_repo import (
    api_actor,
    check_revision,
    dataset_scope,
    evidence_for,
    method_max_score,
    scoped_target,
    write_changes,
)
from app.repositories.business_identity import canonical

if TYPE_CHECKING:
    from app.modules.admissions.contracts import CorrectionCreate


def registrations(
    connection: Any,
    user: dict[str, Any],
    dataset_id: int,
    *,
    limit: int = 100,
    offset: int = 0,
    applicant_code: str | None = None,
    program_code: str | None = None,
    workflow: str | None = None,
) -> dict[str, Any]:
    actor = api_actor(connection, user, dataset_id)
    query = """
        FROM app.admission a JOIN app.program p USING(program_id)
        JOIN app.admission_year y USING(admission_year_id)
        JOIN app.admission_method m USING(admission_method_id)
        JOIN LATERAL (
            SELECT i.source_key FROM app.gold_record_identity i
            WHERE i.namespace=:namespace AND i.entity='applicant'
                AND i.target_key->>'applicant_id'=a.applicant_id::text
                AND (CAST(:applicant_key AS text) IS NULL OR i.source_key=CAST(:applicant_key AS text))
            ORDER BY i.source_key LIMIT 1
        ) i ON true
        WHERE (CAST(:program AS text) IS NULL OR p.program_code=CAST(:program AS text))
    """
    if workflow == "enrollment":
        query += " AND upper(a.admission_status)='ACCEPTED' AND a.enrollment_status IN ('PENDING','CONFIRMED','DECLINED')"
    params = {
        "namespace": actor.namespace,
        "applicant_key": canonical([applicant_code]) if applicant_code else None,
        "program": program_code,
        "limit": limit,
        "offset": offset,
    }
    total = connection.execute(text("SELECT count(*) " + query), params).scalar_one()
    rows = connection.execute(
        text(
            "SELECT a.*,p.program_code,y.year_label,m.method_code,i.source_key "
            + query
            + " ORDER BY a.admission_id LIMIT :limit OFFSET :offset"
        ),
        params,
    ).mappings()
    items = []
    for row in rows:
        key = json.loads(row["source_key"])
        if not isinstance(key, list) or len(key) != 1:
            raise AdmissionRuleError("ADMISSION_APPLICANT_MAPPING_INVALID")
        items.append(
            {
                n: row[n]
                for n in (
                    "admission_id",
                    "program_code",
                    "year_label",
                    "method_code",
                    "admission_score",
                    "admission_status",
                    "enrollment_status",
                    "enrollment_date",
                    "revision",
                )
            }
            | {"applicant_code": key[0]}
        )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def _require_manager(actor: AdmissionActor, connection: Any) -> None:
    if actor.roles.isdisjoint(CORRECTION_ROLES):
        raise AdmissionRuleError("ADMISSION_CORRECTION_FORBIDDEN")
    dataset_scope(connection, actor.dataset_id, actor.roles - {"DATA_ADMIN"})


def _planned(
    connection: Any, current: dict[str, Any], operation: str, patch: dict[str, Any]
) -> dict[str, Any]:
    typed = dict(patch)
    if typed.get("admission_score") is not None:
        typed["admission_score"] = Decimal(str(typed["admission_score"]))
    if typed.get("enrollment_date") is not None:
        typed["enrollment_date"] = date.fromisoformat(typed["enrollment_date"])
    if operation == "RESULT_CORRECTION":
        return result_changes(
            current,
            typed,
            method_max_score(connection, current["admission_method_id"]),
            correction=True,
        )
    return enrollment_changes(current, typed, operation=operation)


def create_correction(
    connection: Any, user: dict[str, Any], request: CorrectionCreate
) -> dict[str, Any]:
    actor = api_actor(connection, user, request.dataset_id)
    _require_manager(actor, connection)
    connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
        {"key": "admission.correction:" + str(request.correction_id)},
    )
    existing = (
        connection.execute(
            select(CORRECTIONS).where(CORRECTIONS.c.correction_id == str(request.correction_id))
        )
        .mappings()
        .first()
    )
    payload = {
        "correction_id": str(request.correction_id),
        "dataset_id": request.dataset_id,
        "admission_id": request.admission_id,
        "expected_revision": request.expected_revision,
        "operation": request.operation,
        "changes": request.changes,
        "reason": request.reason,
        "requested_by": actor.user_id,
    }
    if existing is not None:
        if any(existing[n] != v for n, v in payload.items()):
            raise AdmissionRuleError("ADMISSION_CORRECTION_ID_CONFLICT")
        return dict(existing)
    current = scoped_target(connection, actor, request.admission_id)
    check_revision(connection, current, request.expected_revision)
    changes = _planned(connection, current, request.operation, request.changes)
    preview = json.loads(
        packed(
            {
                "before": current,
                "after": {**current, **changes, "revision": current["revision"] + bool(changes)},
                "fields": list(changes),
            }
        )
    )
    return dict(
        connection.execute(
            insert(CORRECTIONS)
            .values(
                **payload, status="REQUESTED", created_at=func.now(), result={"preview": preview}
            )
            .returning(*CORRECTIONS.c)
        )
        .mappings()
        .one()
    )


def correction(
    connection: Any, user: dict[str, Any], correction_id: str, *, lock: bool = False
) -> tuple[AdmissionActor, dict[str, Any]]:
    # Role is checked before exposing a request or its preview.
    query = select(CORRECTIONS).where(CORRECTIONS.c.correction_id == correction_id)
    if lock:
        query = query.with_for_update()
    row = connection.execute(query).mappings().first()
    if row is None:
        raise AdmissionRuleError("ADMISSION_CORRECTION_NOT_FOUND")
    actor = api_actor(connection, user, row["dataset_id"])
    _require_manager(actor, connection)
    scoped_target(connection, actor, row["admission_id"], lock=False)
    return actor, dict(row)


def apply_correction(connection: Any, user: dict[str, Any], correction_id: str) -> dict[str, Any]:
    actor, request = correction(connection, user, correction_id, lock=True)
    if request["status"] == "APPLIED":
        return request
    if request["status"] != "REQUESTED":
        raise AdmissionRuleError("ADMISSION_CORRECTION_NOT_PENDING")
    current = scoped_target(connection, actor, request["admission_id"])
    check_revision(connection, current, request["expected_revision"])
    changes = _planned(connection, current, request["operation"], request["changes"])
    after = write_changes(
        connection,
        current,
        changes,
        evidence_for(
            actor,
            operation=request["operation"],
            correction_id=correction_id,
            request_key=correction_id.replace("-", ""),
            reason=request["reason"],
            source={"requested_by": request["requested_by"], "channel": "ADMISSIONS_CORRECTION"},
        ),
    )
    result = json.loads(packed({"before": current, "after": after, "fields": list(changes)}))
    return dict(
        connection.execute(
            update(CORRECTIONS)
            .where(CORRECTIONS.c.correction_id == correction_id)
            .values(
                status="APPLIED",
                applied_at=func.now(),
                applied_by=actor.user_id,
                result=result,
            )
            .returning(*CORRECTIONS.c)
        )
        .mappings()
        .one()
    )


def history(
    connection: Any,
    user: dict[str, Any],
    dataset_id: int,
    admission_id: int,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    actor = api_actor(connection, user, dataset_id)
    scoped_target(connection, actor, admission_id, lock=False)
    where = EVENTS.c.admission_id == admission_id
    total = connection.execute(select(func.count()).select_from(EVENTS).where(where)).scalar_one()
    rows = connection.execute(
        select(EVENTS)
        .where(where)
        .order_by(EVENTS.c.created_at, EVENTS.c.event_id)
        .limit(limit)
        .offset(offset)
    ).mappings()
    return {"items": [dict(row) for row in rows], "total": total, "limit": limit, "offset": offset}
