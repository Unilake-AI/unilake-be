"""Phân tích ý định cho câu hỏi bằng ngôn ngữ tự nhiên."""

from __future__ import annotations


def parse_intent(question: str) -> str:
    """Trả về nhãn ý định tổng quát cho câu hỏi."""
    normalized = question.strip().lower()
    if any(token in normalized for token in ("bao nhiêu", "count", "total", "tổng")):
        return "aggregate"
    return "lookup"
