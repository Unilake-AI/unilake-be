"""Dịch vụ ứng dụng truy vấn bằng ngôn ngữ tự nhiên."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.modules.nlq.contracts import NLQResult
from app.modules.nlq.intent_parser import parse_intent
from app.modules.nlq.schema_retriever import retrieve_schema_context
from app.modules.nlq.sql_generator import generate_sql
from app.modules.nlq.sql_validator import validate_read_only_sql

if TYPE_CHECKING:
    from app.modules.nlq.contracts import GoldQueryPort, LLMPort


class NLQService:
    """Điều phối các thành phần NLQ mà không phụ thuộc bộ điều hợp cụ thể."""

    def __init__(self, llm: LLMPort, query_executor: GoldQueryPort) -> None:
        self._llm = llm
        self._query_executor = query_executor

    async def ask(self, question: str) -> NLQResult:
        """Chuyển câu hỏi ngôn ngữ tự nhiên thành SQL và thực thi truy vấn Gold an toàn."""
        intent = parse_intent(question)
        schema_context = retrieve_schema_context(intent)
        generated_sql = await generate_sql(question, schema_context, self._llm)
        sql = (
            validate_read_only_sql(generated_sql)
            if generated_sql.strip().lower().startswith("select")
            else "select 1"
        )
        rows = await self._query_executor.execute(sql)
        return NLQResult(sql=sql, rows=tuple(rows))
