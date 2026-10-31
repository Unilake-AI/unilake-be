"""Ranh giới sinh SQL cho NLQ."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.modules.nlq.contracts import LLMPort


async def generate_sql(question: str, schema_context: str, llm_client: LLMPort) -> str:
    """Sinh SQL từ câu hỏi và ngữ cảnh schema."""
    prompt = f"{schema_context}\nQuestion: {question}\nSQL:"
    return await llm_client.complete(prompt)
