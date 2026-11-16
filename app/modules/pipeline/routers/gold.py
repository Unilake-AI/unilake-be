"""API theo dõi Gold và xem báo cáo trong phạm vi được cấp quyền."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Generator
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.core.deps import require_role
from app.core.exceptions import AppException
from app.core.roles import DATA_WRITE_ROLES, RoleCode
from app.modules.pipeline.repositories.management_db import management_engine
from app.modules.pipeline.services.gold_engine import DIMENSIONS, RECIPE_VERSION

router = APIRouter(prefix="/pipeline/gold", tags=["gold"])
Admin = Annotated[dict[str, Any], Depends(require_role(*DATA_WRITE_ROLES))]
Reader = Annotated[dict[str, Any], Depends(require_role(*RoleCode))]
Domain = Literal["hr", "academic", "admissions"]
STATUS = Literal["QUEUED", "RUNNING", "RETRY_WAIT", "SUCCEEDED", "FAILED", "SUPERSEDED"]


def gold_engine() -> Generator[Engine, None, None]:
    # Dùng cùng cách tạo kết nối nhưng trả đúng thông báo khi cấu trúc Gold chưa sẵn sàng.
    generator = management_engine()
    engine = next(generator)
    try:
        yield engine
    except SQLAlchemyError:
        raise AppException(
            "Chưa truy cập được dữ liệu Gold. Kiểm tra kết nối và migration Gold.",
            code="GOLD_METADATA_UNAVAILABLE",
            status_code=503,
        ) from None
    finally:
        generator.close()


DB = Annotated[Engine, Depends(gold_engine)]


def access(
    user: dict[str, Any], domain: str | None, alias: str = "s"
) -> tuple[str, dict[str, Any]]:
    roles = user.get("roles", [])
    roles = [roles] if isinstance(roles, str) else roles
    allowed = {
        name
        for name in DIMENSIONS
        if any(f"{name.upper()}_{kind}" in roles for kind in ("STAFF", "MANAGER"))
    }
    if not DATA_WRITE_ROLES.isdisjoint(roles) or RoleCode.VIEWER in roles:
        allowed = set(DIMENSIONS)
    if domain is not None:
        allowed &= {domain}
    return f"{alias}.domain = ANY(:domains)", {"domains": sorted(allowed)}


@router.get("/recipes", summary="Xem các phép tổng hợp Gold đang hỗ trợ")
def recipes(_user: Reader) -> dict[str, Any]:
    return {
        "version": RECIPE_VERSION,
        "metric": "record_count",
        "dimensions": DIMENSIONS,
        "note": "Đếm bản ghi trong từng nguồn Silver; không cộng thành số người duy nhất toàn trường. File chưa xác định loại chỉ có tổng số dòng.",
    }


@router.get("/sources", summary="Xem Silver hiện hành và tiến độ cập nhật Gold")
def sources(
    db: DB,
    user: Reader,
    domain: Domain | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, Any]]:
    clause, params = access(user, domain, "cr")
    with db.connect() as conn:
        rows = (
            conn.execute(
                text(
                    """SELECT h.scope,h.snapshot_id AS silver_snapshot_id,
            cr.dataset_id,cr.domain,d.dataset_name,j.job_id,j.status,j.error_code,j.finished_at,
            CASE WHEN j.status='SUCCEEDED' AND s.active_job_id=j.job_id THEN 'CURRENT'
                 WHEN j.job_id IS NULL THEN 'NOT_STARTED' ELSE j.status END AS sync_status
            FROM app.silver_head h
            JOIN LATERAL (SELECT * FROM app.cleaning_run c WHERE c.silver_snapshot_id=h.snapshot_id
              AND c.publication_status IN ('PUBLISHED','PUBLISHED_WITH_QUARANTINE')
              ORDER BY c.finished_at DESC NULLS LAST,c.run_id LIMIT 1) cr ON TRUE
            JOIN app.dataset d ON d.dataset_id=cr.dataset_id
            LEFT JOIN app.gold_scope s ON s.scope=h.scope AND s.silver_snapshot_id=h.snapshot_id
            LEFT JOIN app.gold_job j ON j.scope=s.scope AND j.generation=s.generation
            WHERE """
                    + clause
                    + " ORDER BY cr.finished_at DESC NULLS LAST,h.scope LIMIT :limit OFFSET :offset"
                ),
                {**params, "limit": limit, "offset": offset},
            )
            .mappings()
            .all()
        )
    return [dict(row) for row in rows]


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    silver_snapshot_id: UUID


@router.post("/refresh", status_code=202, summary="Tạo lượt Gold cho bản Silver đang sử dụng")
def refresh(request: RefreshRequest, db: DB, _user: Admin) -> dict[str, Any]:
    with db.begin() as conn:
        scope = conn.execute(
            text("SELECT scope FROM app.silver_head WHERE snapshot_id=:id FOR UPDATE"),
            {"id": str(request.silver_snapshot_id)},
        ).scalar_one_or_none()
        if scope is None:
            raise AppException(
                "Bản Silver này không còn được sử dụng",
                code="GOLD_SILVER_HEAD_CHANGED",
                status_code=409,
            )
        job_id = conn.execute(
            text("SELECT app.enqueue_gold(:scope)"), {"scope": scope}
        ).scalar_one()
        if job_id is None:
            raise AppException(
                "Chưa đủ thông tin nguồn gốc Silver để tạo Gold",
                code="GOLD_SOURCE_EVIDENCE_REQUIRED",
                status_code=422,
            )
        row = (
            conn.execute(
                text("SELECT job_id,status,silver_snapshot_id FROM app.gold_job WHERE job_id=:id"),
                {"id": job_id},
            )
            .mappings()
            .one()
        )
    return dict(row)


@router.get("/jobs", summary="Xem lịch sử xử lý Gold")
def jobs(
    db: DB,
    user: Reader,
    domain: Domain | None = None,
    dataset_id: int | None = None,
    status: STATUS | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, Any]]:
    clause, params = access(user, domain, "j")
    with db.connect() as conn:
        rows = (
            conn.execute(
                text(
                    """SELECT j.job_id,j.scope,j.silver_snapshot_id,j.generation,j.recipe_version,
            j.status,j.attempt_number,j.created_at,j.finished_at,j.error_code,j.dataset_id,j.domain
            FROM app.gold_job j JOIN app.gold_scope s USING(scope) WHERE """
                    + clause
                    + " AND (CAST(:dataset AS bigint) IS NULL OR j.dataset_id=:dataset) AND (CAST(:status AS text) IS NULL OR j.status=:status) ORDER BY j.created_at DESC,j.job_id LIMIT :limit OFFSET :offset"
                ),
                {
                    **params,
                    "dataset": dataset_id,
                    "status": status,
                    "limit": limit,
                    "offset": offset,
                },
            )
            .mappings()
            .all()
        )
    return [dict(row) for row in rows]


def read_job(db: Engine, user: dict[str, Any], job_id: str) -> dict[str, Any]:
    clause, params = access(user, None, "j")
    with db.connect() as conn:
        row = (
            conn.execute(
                text("SELECT j.* FROM app.gold_job j WHERE j.job_id=:id AND " + clause),
                {**params, "id": job_id},
            )
            .mappings()
            .first()
        )
    if row is None:
        raise AppException(
            "Không tìm thấy lượt Gold trong phạm vi được xem",
            code="GOLD_JOB_NOT_FOUND",
            status_code=404,
        )
    return {key: value for key, value in row.items() if key != "lease_token"}


@router.get("/jobs/{job_id}", summary="Xem kết quả và nguồn gốc Silver của lượt Gold")
def job(job_id: UUID, db: DB, user: Reader) -> dict[str, Any]:
    return read_job(db, user, str(job_id))


@router.post("/jobs/{job_id}/retry", status_code=202, summary="Yêu cầu chạy lại lượt Gold bị lỗi")
def retry(job_id: UUID, db: DB, user: Admin) -> dict[str, Any]:
    read_job(db, user, str(job_id))
    with db.begin() as conn:
        row = (
            conn.execute(
                text("""UPDATE app.gold_job j SET status='QUEUED',next_attempt_at=now(),
            error_code=NULL,finished_at=NULL,max_attempts=attempt_number+3
            FROM app.gold_scope s,app.silver_head h
            WHERE j.job_id=:id AND j.scope=s.scope AND h.scope=s.scope
            AND j.generation=s.generation AND h.snapshot_id=j.silver_snapshot_id
            AND j.status='FAILED' RETURNING j.job_id,j.status"""),
                {"id": str(job_id)},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise AppException(
                "Chỉ chạy lại lượt lỗi của bản Silver hiện hành",
                code="GOLD_RETRY_NOT_ALLOWED",
                status_code=409,
            )
    return dict(row)


@router.get("/metrics", summary="Xem số liệu Gold hiện hành trong phạm vi được cấp quyền")
def metrics(
    db: DB,
    user: Reader,
    domain: Domain | None = None,
    dataset_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, Any]]:
    clause, params = access(user, domain, "m")
    with db.connect() as conn:
        rows = (
            conn.execute(
                text(
                    "SELECT m.*,d.dataset_name FROM gold.current_metrics m JOIN app.dataset d ON d.dataset_id=m.dataset_id WHERE "
                    + clause
                    + " AND (CAST(:dataset AS bigint) IS NULL OR m.dataset_id=:dataset) ORDER BY m.scope,m.entity,m.metric,m.dimensions LIMIT :limit OFFSET :offset"
                ),
                {**params, "dataset": dataset_id, "limit": limit, "offset": offset},
            )
            .mappings()
            .all()
        )
    return [dict(row) for row in rows]


@router.get("/jobs/{job_id}/report", summary="Tải toàn bộ số liệu của một bản Gold dưới dạng CSV")
def report(job_id: UUID, db: DB, user: Reader) -> Response:
    item = read_job(db, user, str(job_id))
    if item["status"] != "SUCCEEDED":
        raise AppException(
            "Lượt Gold chưa công bố báo cáo", code="GOLD_REPORT_NOT_READY", status_code=409
        )
    with db.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT entity,metric,dimensions,value FROM gold.aggregate WHERE job_id=:id ORDER BY entity,metric,dimensions"
            ),
            {"id": str(job_id)},
        ).all()
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["Loại dữ liệu", "Chỉ số", "Nhóm dữ liệu", "Số lượng"])
    writer.writerows(
        (
            row.entity,
            row.metric,
            json.dumps(row.dimensions, ensure_ascii=False, sort_keys=True),
            row.value,
        )
        for row in rows
    )
    return Response(
        content="\ufeff" + stream.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="gold-{job_id}.csv"'},
    )


@router.get("/workers", summary="Xem bộ xử lý Gold và thời điểm còn hoạt động")
def workers(db: DB, _user: Admin) -> list[dict[str, Any]]:
    with db.connect() as conn:
        return [
            dict(row)
            for row in conn.execute(
                text(
                    "SELECT *,heartbeat_at>now()-interval '45 seconds' AS healthy FROM app.gold_worker ORDER BY heartbeat_at DESC"
                )
            ).mappings()
        ]


@router.get("/jobs/{job_id}/attempts", summary="Xem lịch sử từng lần thực hiện lượt Gold")
def attempts(job_id: UUID, db: DB, user: Reader) -> list[dict[str, Any]]:
    read_job(db, user, str(job_id))
    with db.connect() as conn:
        return [
            dict(row)
            for row in conn.execute(
                text(
                    "SELECT attempt_number,status,worker_id,error_code,started_at,finished_at FROM app.gold_attempt WHERE job_id=:id ORDER BY attempt_number"
                ),
                {"id": str(job_id)},
            ).mappings()
        ]
