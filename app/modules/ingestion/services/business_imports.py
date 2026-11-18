"""Read a sanitized end-to-end import report using ingestion's existing access rules."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from app.modules.ingestion.services.jobs import get_job


async def import_report(job_id: int, db: Any, user: dict[str, Any]) -> dict[str, Any]:
    ingestion = await get_job(job_id, db, user)
    row = (
        (
            await db.execute(
                text("""
        SELECT a.status AS silver_status,a.error_code AS silver_error,a.result AS silver_result,
               g.status AS gold_status,g.job_id AS gold_job_id,
               q.status AS database_status,q.error_report,q.result AS database_result
        FROM app.auto_pipeline_job a
        LEFT JOIN app.gold_job g ON g.silver_snapshot_id=a.result->'publication'->>'snapshot_id'
        LEFT JOIN app.gold_sync_job q ON q.gold_job_id=g.job_id
        WHERE a.ingestion_job_id=:id ORDER BY a.created_at DESC,g.created_at DESC LIMIT 1
    """),
                {"id": job_id},
            )
        )
        .mappings()
        .first()
    )
    result = dict(row) if row is not None else {}
    silver_result = result.pop("silver_result", None) or {}
    result["ingestion_job_id"] = job_id
    result["bronze_status"] = ingestion.status
    result["error_report"] = result.get("error_report") or silver_result.get("error_report")
    result["database_committed"] = result.get("database_status") == "SYNCED"
    return result
