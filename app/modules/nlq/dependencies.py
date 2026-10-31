"""Các token dependency được bộ điều hợp giao tiếp NLQ cung cấp."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.modules.nlq.service import NLQService


def get_nlq_service() -> NLQService:
    """Yêu cầu điểm lắp ghép ứng dụng cung cấp dịch vụ ứng dụng NLQ."""
    raise RuntimeError("NLQ service provider is not configured")


NLQServiceDependency = Annotated[NLQService, Depends(get_nlq_service)]
