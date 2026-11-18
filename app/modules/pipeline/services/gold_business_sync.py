"""Công bố Gold chi tiết, đọc lại Gold rồi đồng bộ vào PostgreSQL theo khóa nguồn."""

from __future__ import annotations

import gzip
import json
from dataclasses import asdict
from datetime import UTC, datetime
from io import BytesIO
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pyarrow.parquet as pq
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.roles import DATA_WRITE_ROLES, UPLOAD_STAFF_DOMAINS
from app.modules.admissions.contracts import AdmissionRuleError
from app.modules.pipeline.repositories.silver_storage import MinioSilverStore, ObjectRef, checksum
from app.providers import gold_writer_client
from app.repositories.admission_workflow_repo import actor_roles, load_import_actor
from app.repositories.business_import_repo import (
    PREPARATION_ENTITIES,
    describe_import_plan,
    plan_import_records,
)
from app.repositories.gold_business_sync_repo import BusinessSyncBlockedError, sync_records

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

RECIPE = "gold-business-records-v1"


def _prepare(job: dict[str, Any], silver: MinioSilverStore, gold: MinioSilverStore) -> ObjectRef:
    ref = job["silver_manifest_ref"]
    source_ref = ObjectRef(**(json.loads(ref) if isinstance(ref, str) else ref))
    source = json.loads(silver.get(source_ref))
    if (
        source.get("snapshot_id") != job["silver_snapshot_id"]
        or source.get("scope") != job["scope"]
        or source.get("mode") != "production"
        or source.get("activation_allowed") is not True
    ):
        raise ValueError("GOLD_SYNC_SOURCE_INVALID")
    entities = {}
    total_bytes = 0
    total_rows = 0
    prefix = (
        "gold/production/"
        + checksum(job["scope"].encode())
        + "/"
        + job["gold_job_id"]
        + "/business/"
    )
    for entity, artifact in source["entities"].items():
        input_ref = ObjectRef(**artifact.get("business_object", artifact["object"]))
        total_bytes += input_ref.size
        total_rows += artifact["rows"]
        if total_bytes > 200 * 1024 * 1024 or total_rows > 1_000_000:
            raise ValueError("GOLD_SYNC_INPUT_LIMIT_EXCEEDED")
        data = silver.get(input_ref)
        if artifact.get("business_object"):
            core = pq.read_table(BytesIO(silver.get(ObjectRef(**artifact["object"]))))
            operational = pq.read_table(BytesIO(data))
            if (
                set(operational.column_names)
                - set(core.column_names)
                - {"full_name", "_business_import_row"}
                or not set(core.column_names) <= set(operational.column_names)
                or not core.equals(operational.select(core.column_names))
            ):
                raise ValueError("GOLD_SYNC_BUSINESS_EVIDENCE_MISMATCH")
            if "_business_import_row" in operational.column_names and not source.get(
                "business_import"
            ):
                raise ValueError("GOLD_SYNC_BUSINESS_EVIDENCE_MISMATCH")
        if pq.read_metadata(BytesIO(data)).num_rows != artifact["rows"]:
            raise ValueError("GOLD_SYNC_ROW_COUNT_MISMATCH")
        if not artifact.get("lineage") and artifact["rows"]:
            raise ValueError("GOLD_SYNC_SOURCE_EVIDENCE_REQUIRED")
        for batch in artifact.get("lineage", []):
            if batch["key"]["domain_id"] != job["domain"] or str(
                (batch.get("provenance") or {}).get("dataset_id")
            ) != str(job["dataset_id"]):
                raise ValueError("GOLD_SYNC_SOURCE_EVIDENCE_REQUIRED")
        output = gold.put(prefix + entity + "-" + checksum(data) + ".parquet", data)
        gold.get(output)
        entities[entity] = {
            "object": asdict(output),
            "rows": artifact["rows"],
            "source": asdict(input_ref),
        }
    manifest = {
        "format": "unilake-gold-records-v1",
        "recipe_version": RECIPE,
        "gold_job_id": job["gold_job_id"],
        "scope": job["scope"],
        "silver_snapshot_id": job["silver_snapshot_id"],
        "source_manifest": asdict(source_ref),
        "entities": entities,
        **({"business_import": source["business_import"]} if source.get("business_import") else {}),
    }
    payload = json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode()
    result = gold.put(prefix + "manifest-" + checksum(payload) + ".json", payload)
    gold.get(result)
    return result


def _records(
    job: dict[str, Any], gold: MinioSilverStore, ref: ObjectRef
) -> dict[str, list[dict[str, Any]]]:
    manifest = json.loads(gold.get(ref))
    if (
        manifest.get("format") != "unilake-gold-records-v1"
        or manifest.get("recipe_version") != RECIPE
        or manifest.get("gold_job_id") != job["gold_job_id"]
        or manifest.get("scope") != job["scope"]
        or manifest.get("silver_snapshot_id") != job["silver_snapshot_id"]
    ):
        raise ValueError("GOLD_SYNC_MANIFEST_INVALID")
    payload = {}
    prefix = (
        "gold/production/"
        + checksum(job["scope"].encode())
        + "/"
        + job["gold_job_id"]
        + "/business/"
    )
    total_bytes = 0
    total_rows = 0
    for entity, artifact in manifest["entities"].items():
        object_ref = ObjectRef(**artifact["object"])
        total_bytes += object_ref.size
        total_rows += artifact["rows"]
        if (
            not object_ref.key.startswith(prefix)
            or total_bytes > 200 * 1024 * 1024
            or total_rows > 1_000_000
        ):
            raise ValueError("GOLD_SYNC_MANIFEST_INVALID")
        table = pq.read_table(BytesIO(gold.get(object_ref)))
        if table.num_rows != artifact["rows"]:
            raise ValueError("GOLD_SYNC_ROW_COUNT_MISMATCH")
        payload[entity] = table.to_pylist()
        if manifest.get("business_import"):
            for record in payload[entity]:
                record["_business_import_contract"] = manifest["business_import"]
    return payload


def _actor(
    conn: Any,
    job: dict[str, Any],
    entity: str | None,
    namespace: str | None,
    *,
    lock: bool = True,
) -> Any:
    if entity in {
        "import_admissions_registration_v1",
        "import_admissions_results_v1",
        "import_admissions_enrollment_v1",
    }:
        try:
            actor = load_import_actor(conn, job, lock=lock)
            if namespace is not None and actor.namespace != namespace:
                raise AdmissionRuleError("ADMISSION_DATASET_FORBIDDEN")
            return actor
        except AdmissionRuleError as exc:
            raise BusinessSyncBlockedError(exc.code, "admission", exc.field) from None
    if entity not in PREPARATION_ENTITIES:
        return None
    row = conn.execute(
        text(
            """
            SELECT ij.requested_by
            FROM app.cleaning_run c
            JOIN app.ingestion_job ij ON ij.ingestion_job_id=c.ingestion_job_id
            JOIN app.dataset d ON d.dataset_id=ij.dataset_id
            JOIN app.source_system s ON s.source_system_id=d.source_system_id
            WHERE c.silver_snapshot_id=:silver_snapshot_id AND ij.dataset_id=:dataset_id
                AND d.domain=:domain AND d.status='ACTIVE' AND s.is_active
                AND d.source_system_id=CAST(:namespace AS bigint)
                AND c.publication_status IN ('PUBLISHED','PUBLISHED_WITH_QUARANTINE')
            ORDER BY c.finished_at DESC NULLS LAST LIMIT 1
        """
            + (" FOR SHARE OF d,s" if lock else "")
        ),
        {**job, "namespace": namespace},
    ).first()
    if row is not None and str(row[0]).isdecimal():
        try:
            roles = actor_roles(conn, int(row[0]), lock=lock)
            if roles & DATA_WRITE_ROLES or any(
                UPLOAD_STAFF_DOMAINS.get(role) == job["domain"] for role in roles
            ):
                return None
        except AdmissionRuleError:
            pass
    raise BusinessSyncBlockedError("IMPORT_ACTOR_FORBIDDEN", entity)


def _prepare_import(
    engine: Engine,
    job: dict[str, Any],
    gold: MinioSilverStore,
    source_ref: ObjectRef,
    entity: str,
    records: list[dict[str, Any]],
) -> str | None:
    preview: dict[str, Any] = {
        "attempt_number": job["attempt_number"],
        "silver_snapshot_id": job["silver_snapshot_id"],
    }
    preparation: dict[str, Any] = {"preview": preview}
    expected_plan = None
    try:
        with engine.begin() as conn:
            conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            namespace = str(records[0]["_source_system_id"])
            actor = _actor(conn, job, entity, namespace, lock=False)
            planner = plan_import_records(conn, entity, records, actor=actor, lock_rows=False)
        buffer = BytesIO()
        with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as archive:
            archive.write((json.dumps(asdict(source_ref), sort_keys=True) + "\n").encode())
            summary = describe_import_plan(planner, len(records), archive)
        expected_plan = summary.pop("plan_fingerprint")
        preview.update(summary)
        data = buffer.getvalue()
        prefix = source_ref.key.rsplit("/", 1)[0]
        plan_ref = gold.put(f"{prefix}/plan-{checksum(data)}.gz", data)
        gold.get(plan_ref)
        preparation["plan_ref"] = asdict(plan_ref)
        preparation["plan_fingerprint"] = expected_plan
        preview["status"] = "READY"
    except BusinessSyncBlockedError as exc:
        preview.update(status="BLOCKED", error_report=exc.report())

    preview["prepared_at"] = datetime.now(UTC).isoformat()
    with engine.begin() as conn:
        failed = preview.get("error_report")
        changed = conn.execute(
            text("""
                UPDATE app.gold_sync_job
                SET preparation=CAST(:preparation AS jsonb),
                    source_manifest_ref=CAST(:source_ref AS jsonb),
                    status=CASE WHEN :blocked THEN 'BLOCKED' ELSE status END,
                    error_code=:error_code,error_report=CAST(:error_report AS jsonb),
                    finished_at=CASE WHEN :blocked THEN now() ELSE finished_at END,
                    lease_token=CASE WHEN :blocked THEN NULL ELSE lease_token END,
                    lease_until=CASE WHEN :blocked THEN NULL ELSE now()+interval '5 minutes' END
                WHERE gold_job_id=:gold_job_id AND status='RUNNING'
                    AND lease_token=:token AND lease_until>clock_timestamp()
            """),
            {
                **job,
                "preparation": json.dumps(preparation),
                "source_ref": json.dumps(asdict(source_ref)),
                "blocked": failed is not None,
                "error_code": failed["code"] if failed else None,
                "error_report": json.dumps(failed) if failed else None,
            },
        ).rowcount
        if changed != 1:
            raise ValueError("GOLD_SYNC_LEASE_EXPIRED")
    return expected_plan


def run_once(engine: Engine) -> bool:
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE app.gold_sync_job SET status='QUEUED',lease_token=NULL,lease_until=NULL WHERE status='RUNNING' AND lease_until<now()"
            )
        )
        conn.execute(
            text(
                "UPDATE app.gold_sync_job q SET status='SUPERSEDED',finished_at=now() FROM app.gold_job j,app.gold_scope s WHERE q.gold_job_id=j.job_id AND j.scope=s.scope AND (s.active_job_id IS DISTINCT FROM j.job_id OR s.generation<>j.generation) AND q.status IN ('QUEUED','BLOCKED','FAILED')"
            )
        )
        row = (
            conn.execute(
                text("""SELECT q.*,j.scope,j.domain,j.dataset_id,j.silver_snapshot_id,j.silver_manifest_ref,j.generation
          FROM app.gold_sync_job q JOIN app.gold_job j ON j.job_id=q.gold_job_id
          JOIN app.gold_scope s ON s.scope=j.scope AND s.active_job_id=j.job_id AND s.generation=j.generation
          WHERE q.status='QUEUED' AND q.next_attempt_at<=now() ORDER BY q.created_at,q.gold_job_id FOR UPDATE OF q SKIP LOCKED LIMIT 1""")
            )
            .mappings()
            .first()
        )
        if row is None:
            return False
        job = dict(row)
        job["token"] = str(uuid4())
        job["attempt_number"] += 1
        conn.execute(
            text(
                "UPDATE app.gold_sync_job SET status='RUNNING',lease_token=:token,lease_until=now()+interval '5 minutes',attempt_number=:attempt_number,error_code=NULL,error_report=NULL,preparation=NULL WHERE gold_job_id=:gold_job_id"
            ),
            job,
        )
    try:
        if job["recipe_version"] != RECIPE:
            raise ValueError("GOLD_SYNC_RECIPE_UNSUPPORTED")
        client = gold_writer_client()
        silver = MinioSilverStore(client, settings.minio_bucket_silver)
        gold = MinioSilverStore(client, settings.minio_bucket_gold)
        ref = (
            ObjectRef(**job["source_manifest_ref"])
            if job["source_manifest_ref"]
            else _prepare(job, silver, gold)
        )
        payload = _records(job, gold, ref)
        entity = next(iter(payload)) if len(payload) == 1 else None
        namespace = (
            str(payload[entity][0]["_source_system_id"])
            if entity and entity.startswith("import_") and payload[entity]
            else None
        )
        expected_plan = None
        if entity is not None and entity in PREPARATION_ENTITIES and payload[entity]:
            expected_plan = _prepare_import(engine, job, gold, ref, entity, payload[entity])
            if expected_plan is None:
                return True
        with engine.begin() as conn:
            current = conn.execute(
                text(
                    "SELECT active_job_id,generation FROM app.gold_scope WHERE scope=:scope FOR UPDATE"
                ),
                job,
            ).one()
            lease = conn.execute(
                text(
                    "SELECT 1 FROM app.gold_sync_job WHERE gold_job_id=:gold_job_id AND status='RUNNING' AND lease_token=:token AND lease_until>clock_timestamp() FOR UPDATE"
                ),
                job,
            ).first()
            if lease is None:
                raise ValueError("GOLD_SYNC_LEASE_EXPIRED")
            if current[0] != job["gold_job_id"] or current[1] != job["generation"]:
                conn.execute(
                    text(
                        "UPDATE app.gold_sync_job SET status='SUPERSEDED',finished_at=now(),lease_token=NULL,lease_until=NULL WHERE gold_job_id=:gold_job_id"
                    ),
                    job,
                )
                return True
            conn.execute(
                text(
                    "UPDATE app.gold_sync_job SET source_manifest_ref=CAST(:ref AS jsonb) WHERE gold_job_id=:gold_job_id"
                ),
                {**job, "ref": json.dumps(asdict(ref))},
            )
            try:
                with conn.begin_nested():
                    actor = _actor(conn, job, entity if namespace is not None else None, namespace)
                    counts = sync_records(
                        conn,
                        payload,
                        job["gold_job_id"],
                        domain=job["domain"],
                        actor=actor,
                        expected_plan=expected_plan,
                    )
                changed = conn.execute(
                    text(
                        "UPDATE app.gold_sync_job SET status='SYNCED',error_code=NULL,error_report=NULL,result=CAST(:result AS jsonb),finished_at=now(),lease_token=NULL,lease_until=NULL WHERE gold_job_id=:gold_job_id AND lease_token=:token AND lease_until>clock_timestamp()"
                    ),
                    {
                        **job,
                        "result": json.dumps(
                            {
                                "entities": counts,
                                "records_synced": sum(counts.values()),
                                "source": "GOLD",
                                "recipe_version": RECIPE,
                                **getattr(counts, "report", {}),
                            }
                        ),
                    },
                ).rowcount
                if changed != 1:
                    raise ValueError("GOLD_SYNC_LEASE_EXPIRED")
                conn.execute(
                    text(
                        "UPDATE app.gold_sync_job q SET status='QUEUED',next_attempt_at=now(),error_code=NULL,error_report=NULL,finished_at=NULL WHERE q.status='BLOCKED' AND q.error_code='GOLD_SYNC_REFERENCE_MISSING' AND EXISTS(SELECT 1 FROM app.gold_job j WHERE j.job_id=q.gold_job_id AND j.domain=:domain)"
                    ),
                    job,
                )
            except (BusinessSyncBlockedError, IntegrityError) as exc:
                report = (
                    exc.report()
                    if isinstance(exc, BusinessSyncBlockedError)
                    else {"code": "GOLD_SYNC_BUSINESS_CONSTRAINT"}
                )
                conn.execute(
                    text(
                        "UPDATE app.gold_sync_job SET status='BLOCKED',error_code=:code,error_report=CAST(:report AS jsonb),finished_at=now(),lease_token=NULL,lease_until=NULL WHERE gold_job_id=:gold_job_id AND lease_token=:token"
                    ),
                    {**job, "code": report["code"], "report": json.dumps(report)},
                )
    except Exception as exc:
        code = (
            str(exc)
            if isinstance(exc, ValueError) and str(exc).startswith("GOLD_SYNC_")
            else "GOLD_SYNC_STORAGE_OR_DATABASE_UNAVAILABLE"
        )
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE app.gold_sync_job SET status=CASE WHEN attempt_number<3 AND :retry THEN 'QUEUED' ELSE 'FAILED' END,error_code=:code,next_attempt_at=now()+interval '30 seconds',finished_at=now(),lease_token=NULL,lease_until=NULL WHERE gold_job_id=:gold_job_id AND lease_token=:token AND status='RUNNING'"
                ),
                {**job, "code": code, "retry": not isinstance(exc, ValueError)},
            )
    return True
