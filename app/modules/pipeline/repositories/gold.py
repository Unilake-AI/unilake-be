"""Lưu lịch sử Gold, nhận việc độc lập và ngăn lượt cũ ghi đè dữ liệu mới."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sqlalchemy import text

from app.modules.pipeline.silver.lineage import record_pipeline_lineage_sync

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine


class GoldRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def claim(self, worker_id: str, lease_seconds: int = 120) -> dict[str, Any] | None:
        with self.engine.begin() as conn:
            conn.execute(
                text("""UPDATE app.gold_attempt a SET status='FAILED',error_code='GOLD_LEASE_EXPIRED',finished_at=now()
                FROM app.gold_job j WHERE a.lease_token=j.lease_token AND j.status='RUNNING'
                AND j.lease_until<now() AND a.finished_at IS NULL""")
            )
            conn.execute(
                text("""UPDATE app.gold_job SET
                status=CASE WHEN attempt_number>=max_attempts THEN 'FAILED' ELSE 'RETRY_WAIT' END,
                error_code='GOLD_LEASE_EXPIRED',lease_token=NULL,lease_until=NULL,
                next_attempt_at=now(),finished_at=CASE WHEN attempt_number>=max_attempts THEN now() END
                WHERE status='RUNNING' AND lease_until<now()""")
            )
            conn.execute(
                text("""UPDATE app.gold_job j SET status='SUPERSEDED',finished_at=now()
                FROM app.gold_scope s WHERE j.scope=s.scope AND j.generation<>s.generation
                AND j.status IN ('QUEUED','RETRY_WAIT')""")
            )
            row = (
                conn.execute(
                    text("""SELECT j.*,snap.manifest_ref AS silver_manifest_ref
                FROM app.gold_job j JOIN app.gold_scope s ON s.scope=j.scope AND s.generation=j.generation
                JOIN app.silver_snapshot snap ON snap.snapshot_id=j.silver_snapshot_id
                WHERE j.status IN ('QUEUED','RETRY_WAIT') AND j.next_attempt_at<=now()
                ORDER BY j.created_at,j.job_id FOR UPDATE OF j SKIP LOCKED LIMIT 1""")
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            job = dict(row)
            job["lease_token"] = str(uuid4())
            job["attempt_number"] += 1
            conn.execute(
                text("""UPDATE app.gold_job SET status='RUNNING',worker_id=:worker,
                attempt_number=attempt_number+1,lease_token=:lease_token,
                lease_until=now()+make_interval(secs=>:seconds),error_code=NULL,finished_at=NULL
                WHERE job_id=:job_id"""),
                {**job, "worker": worker_id, "seconds": lease_seconds},
            )
            conn.execute(
                text("""INSERT INTO app.gold_attempt(lease_token,job_id,attempt_number,worker_id)
                VALUES(:lease_token,:job_id,:attempt_number,:worker)"""),
                {**job, "worker": worker_id},
            )
            return job

    def heartbeat(self, worker_id: str, job: dict[str, Any] | None = None) -> bool:
        with self.engine.begin() as conn:
            conn.execute(
                text("""INSERT INTO app.gold_worker(worker_id,heartbeat_at) VALUES(:id,now())
                ON CONFLICT(worker_id) DO UPDATE SET heartbeat_at=now()"""),
                {"id": worker_id},
            )
            if job is None:
                return True
            return (
                conn.execute(
                    text("""UPDATE app.gold_job SET lease_until=now()+interval '120 seconds'
                WHERE job_id=:job_id AND lease_token=:lease_token AND status='RUNNING'
                AND lease_until>now()"""),
                    job,
                ).rowcount
                == 1
            )

    def fail(self, job: dict[str, Any], code: str, retryable: bool) -> None:
        with self.engine.begin() as conn:
            changed = conn.execute(
                text("""UPDATE app.gold_job SET
                status=CASE WHEN :retry AND attempt_number<max_attempts THEN 'RETRY_WAIT' ELSE 'FAILED' END,
                error_code=:code,lease_token=NULL,lease_until=NULL,
                next_attempt_at=now()+make_interval(secs=>30*attempt_number),
                finished_at=CASE WHEN :retry AND attempt_number<max_attempts THEN NULL ELSE now() END
                WHERE job_id=:job_id AND lease_token=:lease_token AND status='RUNNING'
                AND lease_until>now()"""),
                {**job, "code": code, "retry": retryable},
            )
            if changed.rowcount == 1:
                conn.execute(
                    text(
                        "UPDATE app.gold_attempt SET status='FAILED',error_code=:code,finished_at=now() WHERE lease_token=:lease_token"
                    ),
                    {**job, "code": code},
                )

    def publish(
        self,
        job: dict[str, Any],
        manifest_ref: dict[str, Any],
        result: dict[str, Any],
        rows: list[dict[str, Any]],
    ) -> bool:
        with self.engine.begin() as conn:
            # Cùng thứ tự khóa với việc đổi Silver và tạo việc Gold để tránh chờ nhau vòng tròn.
            head = conn.execute(
                text("SELECT snapshot_id FROM app.silver_head WHERE scope=:scope FOR SHARE"), job
            ).scalar_one()
            generation = conn.execute(
                text("SELECT generation FROM app.gold_scope WHERE scope=:scope FOR UPDATE"), job
            ).scalar_one()
            valid = conn.execute(
                text("""SELECT job_id FROM app.gold_job WHERE job_id=:job_id
                AND lease_token=:lease_token AND status='RUNNING' AND lease_until>now() FOR UPDATE"""),
                job,
            ).scalar_one_or_none()
            if valid is None:
                return False
            if head != job["silver_snapshot_id"] or generation != job["generation"]:
                conn.execute(
                    text(
                        "UPDATE app.gold_job SET status='SUPERSEDED',finished_at=now(),lease_token=NULL,lease_until=NULL WHERE job_id=:job_id"
                    ),
                    job,
                )
                conn.execute(
                    text(
                        "UPDATE app.gold_attempt SET status='SUPERSEDED',finished_at=now() WHERE lease_token=:lease_token"
                    ),
                    job,
                )
                return False
            if rows:
                conn.execute(
                    text("""INSERT INTO gold.aggregate(job_id,entity,metric,dimensions,value)
                    VALUES(:job_id,:entity,:metric,CAST(:dimensions AS jsonb),:value)"""),
                    [
                        {
                            **row,
                            "job_id": job["job_id"],
                            "dimensions": json.dumps(row["dimensions"], ensure_ascii=False),
                        }
                        for row in rows
                    ],
                )
            conn.execute(
                text("""UPDATE app.gold_job SET status='SUCCEEDED',finished_at=now(),
                manifest_ref=CAST(:manifest AS jsonb),result=CAST(:result AS jsonb),lease_token=NULL,lease_until=NULL
                WHERE job_id=:job_id"""),
                {**job, "manifest": json.dumps(manifest_ref), "result": json.dumps(result)},
            )
            conn.execute(
                text(
                    "UPDATE app.gold_scope SET active_job_id=:job_id,updated_at=now() WHERE scope=:scope"
                ),
                job,
            )
            conn.execute(
                text(
                    "UPDATE app.gold_attempt SET status='SUCCEEDED',finished_at=now() WHERE lease_token=:lease_token"
                ),
                job,
            )
            record_pipeline_lineage_sync(
                conn,
                source_layer="SILVER",
                source_name=job["silver_snapshot_id"],
                target_layer="GOLD",
                target_name=job["job_id"],
                metadata=result["lineage"],
            )
            return True
