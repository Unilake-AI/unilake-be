"""Các tuyến API truy vấn bằng ngôn ngữ tự nhiên."""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.nlq.dependencies import NLQServiceDependency  # noqa: TC001
from app.modules.nlq.schemas import NaturalLanguageQueryRequest, NaturalLanguageQueryResponse

router = APIRouter(prefix="/nlq", tags=["nlq"])


@router.post("/ask", response_model=NaturalLanguageQueryResponse)
async def ask_nlq(
    payload: NaturalLanguageQueryRequest,
    service: NLQServiceDependency,
) -> NaturalLanguageQueryResponse:
    """Chạy luồng NLQ từ phân tích ý định đến thực thi chỉ đọc."""
    result = await service.ask(payload.question)
    return NaturalLanguageQueryResponse(sql=result.sql, rows=list(result.rows))
