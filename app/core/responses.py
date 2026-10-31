"""Schema phản hồi API dùng chung và metadata phản hồi OpenAPI."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ErrorBody(BaseModel):
    """Đối tượng lỗi chuẩn bên trong cấu trúc lỗi API."""

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None


class ErrorResponse(BaseModel):
    """Cấu trúc phản hồi lỗi API chuẩn được AppException sử dụng."""

    success: Literal[False] = False
    error: ErrorBody


COMMON_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {
        "model": ErrorResponse,
        "description": "Unauthorized - token is missing, invalid, or expired.",
    },
    403: {
        "model": ErrorResponse,
        "description": "Forbidden - current user does not have a required role.",
    },
    404: {
        "model": ErrorResponse,
        "description": "Not found - requested resource does not exist.",
    },
    500: {
        "model": ErrorResponse,
        "description": "Internal server error.",
    },
}
