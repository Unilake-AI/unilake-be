"""Các hàm lắp ghép nối cổng ứng dụng với bộ điều hợp cụ thể."""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from app.infra.duckdb.query import EmptyGoldQueryExecutor
from app.infra.llm import MockLLMClient
from app.infra.minio.client import bronze_reader_client as bronze_reader_client
from app.infra.minio.client import silver_writer_client as silver_writer_client
from app.infra.minio.client import source_reader_client as source_reader_client
from app.infra.minio.client import upload_cleanup_client as upload_cleanup_client
from app.infra.minio.client import upload_reader_client as upload_reader_client
from app.infra.minio.client import upload_writer_client as upload_writer_client
from app.modules.nlq.service import NLQService

if TYPE_CHECKING:
    from app.modules.pipeline.contracts.cleaning import CleaningConnection


def cleaning_connection(*, threads: int, memory_limit: str) -> CleaningConnection:
    """Cấp kết nối riêng cho bộ làm sạch và để nơi gọi chịu trách nhiệm đóng kết nối."""
    from app.infra.duckdb.cleaning import DuckDBCleaningConnection

    return DuckDBCleaningConnection(threads=threads, memory_limit=memory_limit)


@lru_cache(maxsize=1)
def provide_nlq_service() -> NLQService:
    """Tạo dịch vụ NLQ bằng các bộ điều hợp an toàn cho môi trường phát triển."""
    return NLQService(llm=MockLLMClient(), query_executor=EmptyGoldQueryExecutor())
