"""Các hàm lắp ghép nối cổng ứng dụng với bộ điều hợp cụ thể."""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from app.core.exceptions import AppException
from app.infra.minio.client import bronze_reader_client as bronze_reader_client
from app.infra.minio.client import gold_writer_client as gold_writer_client
from app.infra.minio.client import silver_writer_client as silver_writer_client
from app.infra.minio.client import source_reader_client as source_reader_client
from app.infra.minio.client import upload_cleanup_client as upload_cleanup_client
from app.infra.minio.client import upload_reader_client as upload_reader_client
from app.infra.minio.client import upload_writer_client as upload_writer_client

if TYPE_CHECKING:
    from app.modules.nlq.service import NLQService
    from app.modules.pipeline.contracts.cleaning import CleaningConnection


def cleaning_connection(*, threads: int, memory_limit: str) -> CleaningConnection:
    """Cấp kết nối riêng cho bộ làm sạch và để nơi gọi chịu trách nhiệm đóng kết nối."""
    from app.infra.duckdb.cleaning import DuckDBCleaningConnection

    return DuckDBCleaningConnection(threads=threads, memory_limit=memory_limit)


@lru_cache(maxsize=1)
def provide_nlq_service() -> NLQService:
    """Chỉ mở NLQ khi có bộ thực thi thật và kiểm soát phạm vi dữ liệu."""
    raise AppException(
        "Hỏi UniLake AI chưa sẵn sàng. Vui lòng sử dụng báo cáo tổng quan.",
        code="NLQ_UNAVAILABLE",
        status_code=503,
    )
