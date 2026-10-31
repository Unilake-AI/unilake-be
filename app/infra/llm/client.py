"""Các giao diện máy khách LLM."""

from __future__ import annotations


class MockLLMClient:
    """Máy khách LLM có kết quả xác định cho kiểm thử và phát triển cục bộ."""

    async def complete(self, prompt: str) -> str:
        return prompt
