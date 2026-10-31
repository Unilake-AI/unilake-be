from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .contract import ReadRequest

if TYPE_CHECKING:
    from app.modules.datasources.models import DataSource


SUPPORTED_TYPES = {
    "CSV",
    "EXCEL",
    "POSTGRESQL",
}

SOURCE_TYPE_ALIASES = {
    "POSTGRES": "POSTGRESQL",
}


FILE_SOURCE_TYPES = {"CSV", "EXCEL"}


def build_read_request(
    data_source: DataSource,
    *,
    source_table: str,
) -> ReadRequest:
    """Chuyển DataSource trong catalog thành ReadRequest cho Source Reader.

    Hàm này là lớp nối giữa module datasources và các reader cụ thể.
    Datasource lưu cấu hình ở dạng ORM/catalog, còn reader chỉ cần một
    ReadRequest gọn hơn để biết đọc nguồn nào, ở đâu, với cấu hình gì.
    """

    # 1. Chỉ build request từ DataSource đang hoạt động.
    # Nếu nguồn đã bị tắt, ingestion không nên đọc tiếp dù cấu hình vẫn còn trong DB.
    if data_source.is_active is not True:
        raise ValueError("DataSource không hoạt động")

    # 2. Chuẩn hóa source_type để "csv", " CSV " hay alias "postgres"
    # đều được gom về tên chuẩn mà factory/reader hiểu.
    source_type = _normalize_source_type(data_source.source_type)

    if source_type not in SUPPORTED_TYPES:
        raise ValueError(
            f"Unsupported source_type: {source_type}. "
            f"Supported types: {', '.join(sorted(SUPPORTED_TYPES))}"
        )

    # 3. source_table là định danh file/bảng cụ thể cần đọc.
    # Với file source, đây thường là tên file; với PostgreSQL là table hoặc schema.table.
    normalized_source_table = _required_text(source_table, "source_table")

    # 4. configuration phải là dict để reader có thể copy và đọc các key kỹ thuật.
    configuration = data_source.configuration

    if not isinstance(configuration, dict):
        raise ValueError("configuration không hợp lệ")

    # 5. File source bắt buộc có location vì reader cần đường dẫn file/thư mục.
    # PostgreSQL lấy thông tin kết nối từ configuration nên location có thể rỗng.
    location = data_source.location

    if source_type in FILE_SOURCE_TYPES:
        if not isinstance(location, str) or not location.strip():
            raise ValueError("Location không hợp lệ")
        location = location.strip()

    # 6. Tạo ReadRequest độc lập với DataSource gốc.
    # dict(configuration) giúp reader không vô tình mutate JSON config trên ORM object.
    return ReadRequest(
        source_type=source_type,
        location=location,
        configuration=dict(configuration),
        source_table=normalized_source_table,
    )


def _normalize_source_type(source_type: Any) -> str:
    """Chuẩn hóa source_type lấy từ DataSource."""

    normalized = _required_text(source_type, "source_type").upper()

    return SOURCE_TYPE_ALIASES.get(normalized, normalized)


def _required_text(value: Any, field_name: str) -> str:
    """Lấy một giá trị chuỗi bắt buộc và trim khoảng trắng hai đầu."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} không hợp lệ")

    return value.strip()
