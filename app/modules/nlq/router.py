"""NLQ thực thi trên Gold đã lọc quyền, lưu lịch sử từng tài khoản."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app.core.exceptions import AppException
from app.core.responses import COMMON_ERROR_RESPONSES
from app.modules.manager import ai, history
from app.modules.manager.router import DB, Manager  # noqa: TC001
from app.modules.manager.schemas import Domain, ReportFilters

router = APIRouter(prefix="/nlq", tags=["nlq"], responses=COMMON_ERROR_RESPONSES)


class ManagerQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    question: str = Field(min_length=1, max_length=500)
    domain: Domain | None = None
    dataset_id: int | None = Field(default=None, gt=0)


class ManagerQueryResponse(BaseModel):
    query_id: str
    sql: str
    rows: list[dict[str, Any]]
    truncated: bool = False
    note: str = "Kết quả thống kê theo nguồn, không mặc định là số người duy nhất và không xác nhận nguyên nhân."


@router.post("/ask", response_model=ManagerQueryResponse)
async def ask_nlq(payload: ManagerQueryRequest, db: DB, manager: Manager) -> ManagerQueryResponse:
    manager.require_scope(payload.domain)
    started = time.perf_counter()
    ids: list[int] = []
    try:
        sql = ai.validate_sql(
            await ai.generate_sql(
                payload.question,
                [payload.domain] if payload.domain else [d for d, _ in manager.scopes],
            )
        )
        rows, ids = await ai.input_metrics(
            db, manager, ReportFilters(domain=payload.domain, dataset_id=payload.dataset_id)
        )
        result, truncated = await asyncio.to_thread(ai.execute, sql, rows)
    except AppException as exc:
        await history.record(
            db,
            manager,
            payload.question,
            payload.domain,
            ids,
            {},
            int((time.perf_counter() - started) * 1000),
            exc.code,
        )
        # Duy trì lịch sử thất bại dù response là 4xx/5xx.
        await db.commit()
        raise
    output = {"sql": sql, "rows": result, "truncated": truncated}
    query_id = await history.record(
        db,
        manager,
        payload.question,
        payload.domain,
        ids,
        output,
        int((time.perf_counter() - started) * 1000),
    )
    return ManagerQueryResponse(query_id=query_id, **output)
