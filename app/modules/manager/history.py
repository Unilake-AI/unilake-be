"""Lịch sử server: kiểm tra lại dataset hiện hành trước khi trả kết quả cũ."""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.modules.manager.schemas import ReportFilters
from app.repositories.manager_report_repo import dataset_scope

if TYPE_CHECKING:
    from app.modules.manager.access import ManagerAccess


class QueryHistoryItem(BaseModel):
    id: UUID
    question: str
    status: str
    domain: str | None
    duration_ms: int
    row_count: int
    created_at: datetime
    error_code: str | None


class QueryHistoryPage(BaseModel):
    items: list[QueryHistoryItem]
    total: int
    limit: int
    offset: int


class QueryHistoryDetail(QueryHistoryItem):
    result: dict[str, Any] = Field(default_factory=dict)


async def record(
    db: AsyncSession,
    manager: ManagerAccess,
    question: str,
    domain: str | None,
    dataset_ids: list[int],
    result: dict[str, Any],
    duration_ms: int,
    error_code: str | None = None,
) -> str:
    query_id = str(uuid4())
    await db.execute(
        text("""
        INSERT INTO app.manager_query_history(query_id,app_user_id,domain,question,status,dataset_ids,result,error_code,duration_ms)
        VALUES (:id,:user,:domain,:question,:status,CAST(:datasets AS jsonb),CAST(:result AS jsonb),:error,:duration)
    """),
        {
            "id": query_id,
            "user": manager.context.user_id,
            "domain": domain,
            "question": question,
            "status": "FAILED" if error_code else "COMPLETED" if result.get("rows") else "NO_DATA",
            "datasets": json.dumps(dataset_ids),
            "result": json.dumps(result, ensure_ascii=False, default=str),
            "error": error_code,
            "duration": duration_ms,
        },
    )
    return query_id


def history_scope(manager: ManagerAccess, filters: ReportFilters) -> tuple[str, dict[str, Any]]:
    # Không áp domain đã chọn lên kiểm tra toàn bộ tập dataset của kết quả đa domain.
    clause, params = dataset_scope(manager, ReportFilters())
    manager.require_scope(filters.domain)
    params.update(user=manager.context.user_id, selected_domain=filters.domain)
    return (
        "h.app_user_id=:user AND (CAST(:selected_domain AS text) IS NULL OR h.domain=:selected_domain)"
        " AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements_text(h.dataset_ids) AS ids(id)"
        " WHERE NOT EXISTS (SELECT 1 FROM app.dataset d WHERE d.dataset_id::text=ids.id AND "
        + clause
        + "))",
        params,
    )


async def list_history(
    db: AsyncSession, manager: ManagerAccess, filters: ReportFilters
) -> QueryHistoryPage:
    clause, params = history_scope(manager, filters)
    total = int(
        (
            await db.execute(
                text("SELECT count(*) FROM app.manager_query_history h WHERE " + clause), params
            )
        ).scalar_one()
    )
    rows = (
        await db.execute(
            text(
                "SELECT h.query_id AS id,h.question,h.domain,h.status,h.duration_ms,h.created_at,h.error_code,"
                "jsonb_array_length(coalesce(h.result->'rows','[]'::jsonb)) AS row_count"
                " FROM app.manager_query_history h WHERE "
                + clause
                + " ORDER BY h.created_at DESC,h.query_id DESC LIMIT :limit OFFSET :offset"
            ),
            {**params, "limit": filters.limit, "offset": filters.offset},
        )
    ).mappings()
    return QueryHistoryPage(
        items=[QueryHistoryItem(**row) for row in rows],
        total=total,
        limit=filters.limit,
        offset=filters.offset,
    )


async def detail(db: AsyncSession, manager: ManagerAccess, query_id: UUID) -> QueryHistoryDetail:
    clause, params = history_scope(manager, ReportFilters())
    row = (
        (
            await db.execute(
                text(
                    "SELECT h.query_id AS id,h.question,h.domain,h.status,h.duration_ms,h.created_at,h.error_code,h.result,"
                    "jsonb_array_length(coalesce(h.result->'rows','[]'::jsonb)) AS row_count"
                    " FROM app.manager_query_history h WHERE " + clause + " AND h.query_id=:id"
                ),
                {**params, "id": str(query_id)},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise AppException(
            "Không tìm thấy truy vấn trong phạm vi được xem",
            code="QUERY_HISTORY_NOT_FOUND",
            status_code=404,
        )
    return QueryHistoryDetail(**row)
