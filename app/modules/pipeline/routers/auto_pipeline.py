"""API cấu hình tự chạy Silver và theo dõi việc trong phạm vi phòng ban được cấp quyền."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.core.deps import require_role
from app.core.exceptions import AppException
from app.core.roles import DATA_WRITE_ROLES, UPLOAD_STAFF_DOMAINS, UPLOAD_WRITE_ROLES
from app.modules.pipeline.contracts.automation import AUTOMATIC_RECIPE_VERSION
from app.modules.pipeline.repositories.flow_monitor import list_flows
from app.modules.pipeline.repositories.management_db import management_engine
from app.modules.pipeline.services.auto_pipeline import configure
from app.modules.pipeline.services.flow_monitor import FlowPage

router = APIRouter(prefix="/pipeline/auto", tags=["auto-silver"])
DB = Annotated[Engine, Depends(management_engine)]
Admin = Annotated[dict[str, Any], Depends(require_role(*DATA_WRITE_ROLES))]
Reader = Annotated[dict[str, Any], Depends(require_role(*UPLOAD_WRITE_ROLES))]


@router.get("/overview", summary="Giám sát Silver tự động trên toàn hệ thống")
def overview(db: DB, _user: Admin) -> dict[str, Any]:
    with db.connect() as connection:
        counts: dict[str, int] = dict(
            connection.execute(
                text("SELECT status,count(*) FROM app.auto_pipeline_job GROUP BY status")
            ).all()
        )
        readiness = connection.execute(
            text("SELECT to_regprocedure('app.enqueue_default_silver(bigint)') IS NOT NULL")
        ).scalar_one()
        paused = connection.execute(
            text("SELECT count(*) FROM app.auto_pipeline_policy WHERE NOT enabled")
        ).scalar_one()
        healthy = connection.execute(
            text(
                "SELECT count(*) FROM app.silver_worker_heartbeat WHERE last_heartbeat_at>now()-interval '45 seconds'"
            )
        ).scalar_one()
    return {
        "automatic_enabled": readiness,
        "recipe_version": AUTOMATIC_RECIPE_VERSION,
        "total": sum(counts.values()),
        "counts": counts,
        "paused_datasets": paused,
        "healthy_workers": healthy,
    }


@router.get(
    "/flows", response_model=FlowPage, summary="Theo dõi từng bước nạp Bronze đến công bố Silver"
)
def flows(
    db: DB,
    _user: Admin,
    search: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    ingestion_job_id: Annotated[int | None, Query(gt=0)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> FlowPage:
    return list_flows(
        db, search=search, ingestion_job_id=ingestion_job_id, limit=limit, offset=offset
    )


class AutoPolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    enabled: bool = False
    profile_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    allow_partial_publication: bool = False
    max_attempts: int = Field(default=3, ge=1, le=10)
    retry_delay_seconds: int = Field(default=30, ge=1, le=3600)


def _scope(user: dict[str, Any], alias: str = "d") -> tuple[str, dict[str, Any]]:
    roles = user.get("roles", [])
    roles = [roles] if isinstance(roles, str) else roles
    if not DATA_WRITE_ROLES.isdisjoint(roles):
        return "", {}
    domains = [domain for role, domain in UPLOAD_STAFF_DOMAINS.items() if role in roles]
    # Phạm vi lấy từ vai trò trên máy chủ, không nhận domain từ người gọi.
    return f" AND {alias}.domain = ANY(:domains)", {"domains": domains}


@router.put(
    "/datasets/{dataset_id}", summary="Bật hoặc cập nhật quy định tự xử lý Silver của bộ dữ liệu"
)
def put_policy(dataset_id: int, request: AutoPolicyRequest, db: DB, user: Admin) -> dict[str, Any]:
    return configure(db, dataset_id, request.model_dump(), str(user["sub"]))


@router.get("/datasets/{dataset_id}", summary="Xem cấu hình tự xử lý Silver")
def get_policy(dataset_id: int, db: DB, _user: Admin) -> dict[str, Any]:
    with db.connect() as connection:
        row = (
            connection.execute(
                text("SELECT * FROM app.auto_pipeline_policy WHERE dataset_id=:id"),
                {"id": dataset_id},
            )
            .mappings()
            .first()
        )
    if row is None:
        raise AppException(
            "Chưa cấu hình tự chạy cho bộ dữ liệu này",
            code="AUTO_PIPELINE_POLICY_NOT_FOUND",
            status_code=404,
        )
    return dict(row)


@router.get("/jobs", summary="Theo dõi các lượt tự xử lý Silver")
def jobs(
    db: DB,
    user: Reader,
    dataset_id: int | None = None,
    ingestion_job_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, Any]]:
    scope, params = _scope(user)
    filters = ""
    if dataset_id is not None:
        filters += " AND j.dataset_id=:dataset"
        params["dataset"] = dataset_id
    if ingestion_job_id is not None:
        filters += " AND j.ingestion_job_id=:ingestion"
        params["ingestion"] = ingestion_job_id
    with db.connect() as connection:
        rows = (
            connection.execute(
                text(
                    """SELECT j.job_id,j.ingestion_job_id,j.dataset_id,j.status,
            j.attempt_number,j.error_code,j.result,j.created_at,j.finished_at,j.next_attempt_at,
            d.dataset_name,d.domain,i.source_table,coalesce(j.policy->>'mode','custom') AS processing_mode
            FROM app.auto_pipeline_job j JOIN app.dataset d USING(dataset_id)
            JOIN app.ingestion_job i ON i.ingestion_job_id=j.ingestion_job_id
            WHERE TRUE """
                    + scope
                    + filters
                    + " ORDER BY j.created_at DESC,j.job_id LIMIT :limit OFFSET :offset"
                ),
                {**params, "limit": limit, "offset": offset},
            )
            .mappings()
            .all()
        )
    return [dict(row) for row in rows]


def _job(db: Engine, user: dict[str, Any], job_id: str) -> dict[str, Any]:
    scope, params = _scope(user)
    with db.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT j.* FROM app.auto_pipeline_job j JOIN app.dataset d USING(dataset_id) WHERE j.job_id=:id"
                    + scope
                ),
                {"id": job_id, **params},
            )
            .mappings()
            .first()
        )
    if row is None:
        raise AppException(
            "Không tìm thấy lượt xử lý trong phạm vi được xem",
            code="AUTO_PIPELINE_JOB_NOT_FOUND",
            status_code=404,
        )
    return dict(row)


@router.get("/jobs/{job_id}", summary="Xem kết quả tự xử lý Silver")
def get_job(job_id: UUID, db: DB, user: Reader) -> dict[str, Any]:
    row = _job(db, user, str(job_id))
    return {
        key: value
        for key, value in row.items()
        if key not in {"policy", "bronze_input", "lease_token"}
    }


@router.get("/jobs/{job_id}/attempts", summary="Xem lịch sử các lần thử xử lý Silver")
def attempts(job_id: UUID, db: DB, user: Reader) -> list[dict[str, Any]]:
    _job(db, user, str(job_id))
    with db.connect() as connection:
        rows = (
            connection.execute(
                text(
                    "SELECT attempt_number,worker_id,status,error_code,started_at,finished_at FROM app.auto_pipeline_attempt WHERE job_id=:id ORDER BY attempt_number"
                ),
                {"id": str(job_id)},
            )
            .mappings()
            .all()
        )
    return [dict(row) for row in rows]


@router.post("/jobs/{job_id}/retry", summary="Yêu cầu thử lại một lượt Silver đã thất bại")
def retry(job_id: UUID, db: DB, user: Admin) -> dict[str, Any]:
    _job(db, user, str(job_id))
    with db.begin() as connection:
        row = (
            connection.execute(
                text("""UPDATE app.auto_pipeline_job j SET status='QUEUED',
            policy=jsonb_set(j.policy,'{max_attempts}',to_jsonb(j.attempt_number+1)),
            next_attempt_at=NULL,finished_at=NULL,error_code=NULL
            WHERE job_id=:id AND status='FAILED' AND ((
                j.policy->>'mode'='automatic' AND j.policy->>'recipe_version'=:recipe
                AND NOT EXISTS (SELECT 1 FROM app.auto_pipeline_policy p WHERE p.dataset_id=j.dataset_id AND NOT p.enabled)
            ) OR (coalesce(j.policy->>'mode','custom')<>'automatic' AND EXISTS (
                SELECT 1 FROM app.auto_pipeline_policy p WHERE p.dataset_id=j.dataset_id
                AND p.enabled AND p.revision=(j.policy->>'revision')::int)))
            RETURNING job_id,status,attempt_number"""),
                {"id": str(job_id), "recipe": AUTOMATIC_RECIPE_VERSION},
            )
            .mappings()
            .first()
        )
    if row is None:
        raise AppException(
            "Chỉ thử lại lượt thất bại có cấu hình còn hiệu lực",
            code="AUTO_PIPELINE_RETRY_CONFLICT",
            status_code=409,
        )
    return dict(row)


@router.get("/workers", summary="Xem các Silver Worker và thời điểm liên lạc gần nhất")
def workers(db: DB, _user: Admin) -> list[dict[str, Any]]:
    with db.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                text(
                    "SELECT *,last_heartbeat_at>now()-interval '45 seconds' AS healthy FROM app.silver_worker_heartbeat ORDER BY last_heartbeat_at DESC,worker_id"
                )
            ).mappings()
        ]
