"""Cấu hình máy khách MinIO."""

from __future__ import annotations

from dataclasses import dataclass

from minio import Minio
from urllib3 import PoolManager, Timeout

from app.core.config import settings


@dataclass(frozen=True)
class MinioClientSettings:
    """Tùy chọn kết nối cho kho đối tượng Bronze."""

    endpoint: str = settings.minio_endpoint
    access_key: str = settings.minio_access_key
    secret_key: str = settings.minio_secret_key
    secure: bool = settings.minio_secure


def _scoped_client(
    access_key: str, secret_key: str, role: str, *, timeout: float | None = None
) -> Minio:
    """Thiếu SA thì dừng rõ ràng, không dùng root làm phương án dự phòng."""
    if not access_key or not secret_key:
        raise ValueError(f"MINIO_{role}_CREDENTIALS_REQUIRED")
    return Minio(
        settings.minio_endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=settings.minio_secure,
        region=settings.minio_region,
        http_client=PoolManager(timeout=Timeout(total=timeout), retries=False)
        if timeout is not None
        else None,
    )


def bronze_writer_client() -> Minio:
    return _scoped_client(
        settings.bronze_writer_access_key, settings.bronze_writer_secret_key, "BRONZE_WRITER"
    )


def bronze_verifier_client() -> Minio:
    return _scoped_client(
        settings.bronze_verifier_access_key, settings.bronze_verifier_secret_key, "BRONZE_VERIFIER"
    )


def bronze_reader_client(*, timeout: float | None = None) -> Minio:
    return _scoped_client(
        settings.bronze_reader_access_key,
        settings.bronze_reader_secret_key,
        "BRONZE_READER",
        timeout=timeout,
    )


def silver_writer_client() -> Minio:
    return _scoped_client(
        settings.silver_writer_access_key, settings.silver_writer_secret_key, "SILVER_WRITER"
    )


def source_reader_client(bucket: str) -> Minio:
    # Bronze dùng SA downstream; bucket nguồn khác phải có SA đọc nguồn riêng.
    if bucket == settings.minio_bucket_bronze:
        return bronze_reader_client()
    return _scoped_client(
        settings.source_minio_access_key, settings.source_minio_secret_key, "SOURCE_READER"
    )


def upload_writer_client() -> Minio:
    return _scoped_client(
        settings.upload_writer_access_key,
        settings.upload_writer_secret_key,
        "UPLOAD_WRITER",
        timeout=settings.upload_timeout_seconds,
    )


def upload_reader_client() -> Minio:
    return _scoped_client(
        settings.upload_reader_access_key,
        settings.upload_reader_secret_key,
        "UPLOAD_READER",
        timeout=settings.upload_timeout_seconds,
    )


def upload_cleanup_client() -> Minio:
    return _scoped_client(
        settings.upload_cleanup_access_key,
        settings.upload_cleanup_secret_key,
        "UPLOAD_CLEANUP",
        timeout=settings.upload_timeout_seconds,
    )
