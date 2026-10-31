from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.modules.ingestion.readers.request_builder import build_read_request

DEFAULT_CONFIGURATION = {"delimiter": ",", "encoding": "utf-8", "has_header": True}
MISSING_CONFIGURATION = object()


def _data_source(
    *,
    source_type: Any = "CSV",
    location: Any = "/data/inbox",
    configuration: Any = MISSING_CONFIGURATION,
    is_active: bool = True,
) -> Any:
    """Tạo DataSource giả lập với các field mà request_builder cần."""

    # Test chỉ cần object có đủ attribute giống DataSource ORM.
    # Trả về Any để type checker không bắt SimpleNamespace phải là SQLAlchemy DataSource thật.
    return SimpleNamespace(
        source_type=source_type,
        location=location,
        configuration=DEFAULT_CONFIGURATION.copy()
        if configuration is MISSING_CONFIGURATION
        else configuration,
        is_active=is_active,
    )


def test_build_read_request_for_csv_source() -> None:
    """Build ReadRequest thành công từ DataSource CSV đang hoạt động."""

    data_source = _data_source(source_type=" csv ", location=" /data/csv ")

    request = build_read_request(data_source, source_table=" applicant.csv ")

    assert request.source_type == "CSV"
    assert request.location == "/data/csv"
    assert request.configuration == {"delimiter": ",", "encoding": "utf-8", "has_header": True}
    assert request.source_table == "applicant.csv"


def test_build_read_request_for_postgres_alias() -> None:
    """Alias postgres được chuẩn hóa thành POSTGRESQL."""

    data_source = _data_source(
        source_type="postgres",
        location=None,
        configuration={
            "host": "localhost",
            "port": 5432,
            "database": "unilake",
            "username": "reader",
            "password": "secret",
        },
    )

    request = build_read_request(data_source, source_table="public.student")

    assert request.source_type == "POSTGRESQL"
    assert request.location is None
    assert request.source_table == "public.student"


def test_build_read_request_copies_configuration() -> None:
    """ReadRequest giữ bản copy để reader không mutate configuration của DataSource."""

    configuration = {"delimiter": ",", "encoding": "utf-8", "has_header": True}
    data_source = _data_source(configuration=configuration)

    request = build_read_request(data_source, source_table="applicant.csv")
    request.configuration["delimiter"] = ";"

    assert configuration["delimiter"] == ","


def test_build_read_request_rejects_inactive_data_source() -> None:
    """Không build request cho DataSource đã bị tắt."""

    data_source = _data_source(is_active=False)

    with pytest.raises(ValueError, match="DataSource không hoạt động"):
        build_read_request(data_source, source_table="applicant.csv")


def test_build_read_request_rejects_unsupported_source_type() -> None:
    """Báo lỗi khi source_type chưa có reader hỗ trợ."""

    data_source = _data_source(source_type="JSON")

    with pytest.raises(ValueError, match="Unsupported source_type: JSON"):
        build_read_request(data_source, source_table="payload.json")


def test_build_read_request_rejects_missing_source_table() -> None:
    """source_table là bắt buộc để reader biết file/bảng nào cần đọc."""

    data_source = _data_source()

    with pytest.raises(ValueError, match="source_table không hợp lệ"):
        build_read_request(data_source, source_table=" ")


def test_build_read_request_rejects_invalid_configuration() -> None:
    """configuration phải là dict vì reader đọc option theo key."""

    data_source = _data_source(configuration=None)

    with pytest.raises(ValueError, match="configuration không hợp lệ"):
        build_read_request(data_source, source_table="applicant.csv")


def test_build_read_request_rejects_missing_location_for_file_source() -> None:
    """CSV/Excel bắt buộc có location vì reader cần đường dẫn file hoặc thư mục."""

    data_source = _data_source(source_type="EXCEL", location=" ")

    with pytest.raises(ValueError, match="Location không hợp lệ"):
        build_read_request(data_source, source_table="applicant.xlsx")
