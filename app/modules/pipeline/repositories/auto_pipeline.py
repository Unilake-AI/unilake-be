"""Hàng đợi Silver trong PostgreSQL; nhận việc, gia hạn, ghi lịch sử và chạy lại có giới hạn."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.modules.pipeline.repositories.object_metadata import record_heartbeat

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection, Engine


class AutoPipelineRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    @staticmethod
    def fence(connection: Connection, job_id: str, token: str) -> dict[str, Any]:
        """Khóa bản ghi trước khi công bố; worker hết quyền không được thay đổi Silver."""
        row = (
            connection.execute(
                text("""
            SELECT * FROM app.auto_pipeline_job
            WHERE job_id=:id AND lease_token=:token AND status='RUNNING'
                AND lease_expires_at > clock_timestamp() FOR UPDATE
        """),
                {"id": job_id, "token": token},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ValueError("AUTO_PIPELINE_LEASE_LOST")
        return dict(row)

    @staticmethod
    def published(connection: Connection, job_id: str, token: str, result: dict[str, Any]) -> None:
        """Ghi thành công trong chính giao dịch công bố, tránh công bố lại khi worker dừng."""
        AutoPipelineRepository.fence(connection, job_id, token)
        AutoPipelineRepository._complete(connection, job_id, "SUCCESS", result)

    @staticmethod
    def _complete(connection: Connection, job_id: str, status: str, result: dict[str, Any]) -> None:
        connection.execute(
            text("""
            UPDATE app.auto_pipeline_job SET status=:status, result=CAST(:result AS jsonb),
                error_code=NULL, finished_at=now(), lease_expires_at=NULL WHERE job_id=:id
        """),
            {"id": job_id, "status": status, "result": json.dumps(result)},
        )
        connection.execute(
            text("""
            UPDATE app.auto_pipeline_attempt SET status=:status, finished_at=now()
            WHERE job_id=:id AND status='RUNNING'
        """),
            {"id": job_id, "status": status},
        )

    def claim(self, worker_id: str, lease_seconds: int) -> dict[str, Any] | None:
        try:
            with self.engine.begin() as connection:
                # Hết hạn thì đóng lượt cũ trước; người nhận tiếp theo dùng quyền hoàn toàn mới.
                connection.execute(
                    text("""
                    UPDATE app.auto_pipeline_attempt a SET status='EXPIRED',
                        error_code='AUTO_PIPELINE_LEASE_EXPIRED', finished_at=now()
                    FROM app.auto_pipeline_job j WHERE a.job_id=j.job_id AND a.status='RUNNING'
                        AND j.status='RUNNING' AND j.lease_expires_at < now()
                """)
                )
                connection.execute(
                    text("""
                    UPDATE app.auto_pipeline_job SET
                        status=CASE WHEN attempt_number < (policy->>'max_attempts')::int
                            THEN 'RETRY_WAIT' ELSE 'FAILED' END,
                        error_code='AUTO_PIPELINE_LEASE_EXPIRED', next_attempt_at=now(),
                        finished_at=CASE WHEN attempt_number >= (policy->>'max_attempts')::int
                            THEN now() ELSE NULL END,
                        lease_token=NULL, lease_expires_at=NULL
                    WHERE status='RUNNING' AND lease_expires_at < now()
                """)
                )
                # Tắt hoặc thay cấu hình không được âm thầm đổi quy định của việc đã xếp hàng.
                connection.execute(
                    text("""
                    UPDATE app.auto_pipeline_job j SET status='CANCELLED',
                        error_code='AUTO_PIPELINE_POLICY_CHANGED', finished_at=now()
                    WHERE j.status IN ('QUEUED','RETRY_WAIT') AND (
                        (j.policy->>'mode'='automatic' AND EXISTS (
                            SELECT 1 FROM app.auto_pipeline_policy p WHERE p.dataset_id=j.dataset_id AND NOT p.enabled))
                        OR (coalesce(j.policy->>'mode','custom')<>'automatic' AND NOT EXISTS (
                        SELECT 1 FROM app.auto_pipeline_policy p WHERE p.dataset_id=j.dataset_id
                            AND p.enabled AND p.revision=(j.policy->>'revision')::int)))
                """)
                )
                row = (
                    connection.execute(
                        text("""
                    SELECT j.* FROM app.auto_pipeline_job j
                    WHERE (j.status='QUEUED' OR (j.status='RETRY_WAIT' AND j.next_attempt_at<=now()))
                        AND NOT EXISTS (SELECT 1 FROM app.auto_pipeline_job active
                            WHERE active.dataset_id=j.dataset_id AND active.status='RUNNING')
                    ORDER BY j.created_at,j.job_id FOR UPDATE OF j SKIP LOCKED LIMIT 1
                """)
                    )
                    .mappings()
                    .first()
                )
                if row is None:
                    return None
                job = dict(row)
                token = str(uuid4())
                attempt = job["attempt_number"] + 1
                connection.execute(
                    text("""
                    UPDATE app.auto_pipeline_job SET status='RUNNING', attempt_number=:attempt,
                        worker_id=:worker, lease_token=:token,
                        lease_expires_at=now()+make_interval(secs=>:seconds), error_code=NULL
                    WHERE job_id=:id
                """),
                    {
                        "id": job["job_id"],
                        "attempt": attempt,
                        "worker": worker_id,
                        "token": token,
                        "seconds": lease_seconds,
                    },
                )
                connection.execute(
                    text("""
                    INSERT INTO app.auto_pipeline_attempt(job_id,attempt_number,lease_token,worker_id,status)
                    VALUES (:id,:attempt,:token,:worker,'RUNNING')
                """),
                    {"id": job["job_id"], "attempt": attempt, "token": token, "worker": worker_id},
                )
                return {**job, "lease_token": token, "attempt_number": attempt, "status": "RUNNING"}
        except IntegrityError:
            # Worker khác vừa nhận cùng bộ dữ liệu; lần thăm dò sau sẽ chọn việc khác.
            return None

    def heartbeat(self, worker_id: str, job: dict[str, Any] | None, seconds: int) -> bool:
        with self.engine.begin() as connection:
            if connection.dialect.name != "sqlite":
                record_heartbeat(self.engine, worker_id, job["job_id"] if job else None)
            else:
                connection.execute(
                    text("""
                    INSERT INTO app.silver_worker_heartbeat(worker_id,current_job_id,last_heartbeat_at)
                    VALUES (:worker,:id,now()) ON CONFLICT(worker_id) DO UPDATE
                    SET current_job_id=EXCLUDED.current_job_id,last_heartbeat_at=EXCLUDED.last_heartbeat_at
                """),
                    {"worker": worker_id, "id": job["job_id"] if job else None},
                )
            if job is None:
                return True
            return bool(
                connection.execute(
                    text("""
                UPDATE app.auto_pipeline_job SET lease_expires_at=now()+make_interval(secs=>:seconds)
                WHERE job_id=:id AND lease_token=:token AND status='RUNNING'
                    AND lease_expires_at > clock_timestamp() RETURNING job_id
            """),
                    {"id": job["job_id"], "token": job["lease_token"], "seconds": seconds},
                ).first()
            )

    def finish(self, job: dict[str, Any], result: dict[str, Any]) -> None:
        with self.engine.begin() as connection:
            row = connection.execute(
                text("SELECT status FROM app.auto_pipeline_job WHERE job_id=:id"),
                {"id": job["job_id"]},
            ).scalar_one()
            if row == "SUCCESS":
                return  # Kết quả đã được ghi cùng giao dịch công bố Silver.
            self.fence(connection, job["job_id"], job["lease_token"])
            self._complete(connection, job["job_id"], "QUARANTINED", result)

    def fail(
        self, job: dict[str, Any], code: str, retryable: bool, report: dict[str, Any] | None = None
    ) -> None:
        with self.engine.begin() as connection:
            current = self.fence(connection, job["job_id"], job["lease_token"])
            retry = retryable and current["attempt_number"] < job["policy"]["max_attempts"]
            delay = min(
                3600, job["policy"]["retry_delay_seconds"] * 2 ** (current["attempt_number"] - 1)
            )
            connection.execute(
                text("""
                UPDATE app.auto_pipeline_job SET status=:status,error_code=:code,
                    result=coalesce(CAST(:report AS jsonb),result),
                    lease_token=NULL,lease_expires_at=NULL,
                    next_attempt_at=now()+make_interval(secs=>:delay),
                    finished_at=CASE WHEN :terminal THEN now() ELSE NULL END
                WHERE job_id=:id
            """),
                {
                    "id": job["job_id"],
                    "status": "RETRY_WAIT" if retry else "FAILED",
                    "code": code,
                    "delay": delay,
                    "terminal": not retry,
                    "report": json.dumps({"error_report": report}) if report else None,
                },
            )
            connection.execute(
                text("""
                UPDATE app.auto_pipeline_attempt SET status='FAILED',error_code=:code,finished_at=now()
                WHERE job_id=:id AND attempt_number=:attempt
            """),
                {"id": job["job_id"], "attempt": job["attempt_number"], "code": code},
            )
