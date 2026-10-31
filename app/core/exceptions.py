"""Ngoại lệ ứng dụng và các hàm xử lý HTTP."""

from __future__ import annotations

from typing import Any

from fastapi import Request, status
from fastapi.responses import JSONResponse


class AppException(Exception):  # noqa: N818
    """Ngoại lệ nghiệp vụ được biểu diễn theo cấu trúc lỗi API chuẩn."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        code: str = "APP_ERROR",
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message
        self.status_code = status_code
        self.code = code
        self.details = details or {}
        super().__init__(message)


async def app_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """Biểu diễn AppException theo cấu trúc lỗi chung của dự án."""
    if not isinstance(exc, AppException):
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "success": False,
                "error": {
                    "code": "INTERNAL_SERVER_ERROR",
                    "message": "Internal server error",
                    "details": {},
                    "request_id": getattr(request.state, "request_id", None),
                },
            },
        )

    request_id = getattr(request.state, "request_id", None)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
                "request_id": request_id,
            },
        },
    )
