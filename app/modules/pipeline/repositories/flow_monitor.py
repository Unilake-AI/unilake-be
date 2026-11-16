"""Đọc một trang từ lần nạp, kể cả khi chưa có Silver; nối đúng lần thử gần nhất."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from app.modules.pipeline.services.flow_monitor import FlowPage, project_flow

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine


def list_flows(
    engine: Engine, *, search: str | None, ingestion_job_id: int | None, limit: int, offset: int
) -> FlowPage:
    filters = []
    params: dict[str, Any] = {"limit": limit + 1, "offset": offset}
    if search:
        filters.append(
            "(i.source_table ILIKE :search OR d.dataset_name ILIKE :search OR coalesce(u.username,i.requested_by) ILIKE :search)"
        )
        params["search"] = f"%{search}%"
    if ingestion_job_id is not None:
        filters.append("i.ingestion_job_id=:ingestion_job_id")
        params["ingestion_job_id"] = ingestion_job_id
    query = """
        SELECT i.ingestion_job_id,i.dataset_id,d.dataset_name,d.domain,
          d.status AS dataset_status, i.source_table AS filename,
          coalesce(u.username,i.requested_by) AS uploaded_by,i.created_at,
          i.status AS ingestion_status,i.stage AS ingestion_stage,
          i.started_at AS bronze_started_at,i.finished_at AS bronze_finished_at,
          i.rows_written AS bronze_rows,j.job_id AS auto_job_id,j.status AS silver_status,
          coalesce(j.attempt_number,0) AS attempt_number,j.next_attempt_at,
          coalesce(j.error_code,c.error_code) AS error_code,j.result,
          j.created_at AS silver_created_at,j.finished_at AS silver_finished_at,
          coalesce(p.enabled=FALSE,FALSE) AS paused,
          a.started_at AS attempt_started_at,c.run_id,c.cleaning_status,c.publication_status,
          c.started_at AS cleaning_started_at,c.finished_at AS cleaning_finished_at,
          coalesce(c.silver_snapshot_id,j.result->'publication'->>'snapshot_id') AS snapshot_id,
          c.records_read,c.records_passed,c.records_quarantined,c.records_published
        FROM app.ingestion_job i
        JOIN app.dataset d ON d.dataset_id=i.dataset_id
        JOIN app.data_layer l ON l.data_layer_id=i.target_layer_id AND l.layer_code='BRONZE'
        LEFT JOIN app.app_user u ON u.app_user_id::text=i.requested_by
        LEFT JOIN app.auto_pipeline_job j ON j.ingestion_job_id=i.ingestion_job_id
        LEFT JOIN app.auto_pipeline_policy p ON p.dataset_id=i.dataset_id
        LEFT JOIN LATERAL (SELECT started_at FROM app.auto_pipeline_attempt
          WHERE job_id=j.job_id ORDER BY attempt_number DESC LIMIT 1) a ON TRUE
        LEFT JOIN LATERAL (SELECT * FROM app.cleaning_run
          WHERE (auto_job_id=j.job_id AND started_at>=a.started_at)
            OR (j.job_id IS NULL AND ingestion_job_id=i.ingestion_job_id)
          ORDER BY started_at DESC,run_id DESC LIMIT 1) c ON TRUE
        WHERE TRUE
    """
    if filters:
        query += " AND " + " AND ".join(filters)
    query += (
        " ORDER BY i.created_at DESC NULLS LAST,i.ingestion_job_id DESC LIMIT :limit OFFSET :offset"
    )
    with engine.connect() as connection:
        ready = bool(
            connection.execute(
                text("SELECT to_regprocedure('app.enqueue_default_silver(bigint)') IS NOT NULL")
            ).scalar_one()
        )
        rows = list(connection.execute(text(query), params).mappings())
    return FlowPage(
        items=[project_flow(dict(row), ready) for row in rows[:limit]],
        next_offset=offset + limit if len(rows) > limit else None,
    )
