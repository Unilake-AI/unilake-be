"""Các hàm lắp ghép nối cổng ứng dụng với bộ điều hợp cụ thể."""

from __future__ import annotations

from functools import lru_cache

from app.infra.duckdb.query import EmptyGoldQueryExecutor
from app.infra.llm import MockLLMClient
from app.modules.nlq.service import NLQService


@lru_cache(maxsize=1)
def provide_nlq_service() -> NLQService:
    """Tạo dịch vụ NLQ bằng các bộ điều hợp an toàn cho môi trường phát triển."""
    return NLQService(llm=MockLLMClient(), query_executor=EmptyGoldQueryExecutor())
