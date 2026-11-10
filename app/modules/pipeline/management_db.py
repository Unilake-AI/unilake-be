"""Mở kết nối cho các thao tác cấu hình và đọc dữ liệu Silver."""

from __future__ import annotations

from collections.abc import Generator

from minio.error import MinioException
from sqlalchemy import URL, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from urllib3.exceptions import HTTPError

from app.core.config import settings
from app.core.exceptions import AppException
from app.modules.pipeline.silver_storage import SilverError


def management_engine() -> Generator[Engine, None, None]:
    engine = create_engine(
        URL.create(
            "postgresql+psycopg",
            username=settings.db_user,
            password=settings.db_password,
            host=settings.db_host,
            port=settings.db_port,
            database=settings.db_name,
        ),
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
    )
    try:
        yield engine
    except SQLAlchemyError:
        raise AppException(
            "Không thể truy cập cấu hình Silver",
            code="SILVER_METADATA_UNAVAILABLE",
            status_code=503,
        ) from None
    except (MinioException, HTTPError, OSError):
        raise AppException(
            "Không thể truy cập file trên kho dữ liệu",
            code="SILVER_STORAGE_UNAVAILABLE",
            status_code=503,
        ) from None
    except SilverError:
        raise AppException(
            "Phiên bản Silver không hợp lệ hoặc không còn khớp cấu hình",
            code="SILVER_SNAPSHOT_INVALID",
            status_code=422,
        ) from None
    except ValueError:
        raise AppException(
            "Cấu hình hoặc dữ liệu không hợp lệ",
            code="SILVER_CONFIGURATION_INVALID",
            status_code=422,
        ) from None
    finally:
        engine.dispose()
