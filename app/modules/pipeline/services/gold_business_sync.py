"""Công bố Gold chi tiết, đọc lại Gold rồi đồng bộ vào PostgreSQL theo khóa nguồn."""

from __future__ import annotations

import json
from dataclasses import asdict
from io import BytesIO
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pyarrow.parquet as pq
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.modules.pipeline.repositories.silver_storage import MinioSilverStore, ObjectRef, checksum
from app.providers import gold_writer_client
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
                set(operational.column_names) - set(core.column_names) - {"full_name"}
                or not set(core.column_names) <= set(operational.column_names)
                or not core.equals(operational.select(core.column_names))
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
    return payload


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
        conn.execute(
            text(
                "UPDATE app.gold_sync_job SET status='RUNNING',lease_token=:token,lease_until=now()+interval '5 minutes',attempt_number=attempt_number+1,error_code=NULL,error_report=NULL WHERE gold_job_id=:gold_job_id"
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
                    counts = sync_records(conn, payload, job["gold_job_id"])
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
