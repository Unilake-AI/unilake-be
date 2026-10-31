from __future__ import annotations

from typing import TYPE_CHECKING

from .csv_reader import CSVReader
from .excel_reader import ExcelReader
from .postgresql_reader import PostgreSQLReader

if TYPE_CHECKING:
    from .contract import SourceReader


SUPPORTED_SOURCE_TYPES = ("CSV", "EXCEL", "POSTGRESQL")


class SourceReaderFactory:
    """Chọn Source Reader dựa trên source_type."""

    @staticmethod
    def get_reader(source_type: str) -> SourceReader:
        """Trả về reader phù hợp với loại nguồn dữ liệu.

        Factory giúp code ingestion không cần biết class cụ thể nào sẽ đọc CSV,
        Excel hay PostgreSQL. Caller chỉ truyền source_type từ data_source, phần
        còn lại do factory quyết định.
        """

        # 1. Chuẩn hóa source_type để caller truyền "csv", " CSV " hay "CSV"
        # đều cho ra cùng một reader.
        normalized_source_type = _normalize_source_type(source_type)

        # 2. Chọn reader theo source_type đã chuẩn hóa.
        if normalized_source_type == "CSV":
            return CSVReader()

        if normalized_source_type == "EXCEL":
            return ExcelReader()

        if normalized_source_type == "POSTGRESQL":
            return PostgreSQLReader()

        # 3. Nếu source_type chưa được hỗ trợ, báo lỗi rõ danh sách hiện có.
        raise ValueError(
            f"Unsupported source_type: {source_type}. "
            f"Supported types: {', '.join(SUPPORTED_SOURCE_TYPES)}"
        )


def _normalize_source_type(source_type: str) -> str:
    """Chuẩn hóa source_type trước khi so khớp reader.

    Một vài nơi có thể dùng "POSTGRES" như alias ngắn của "POSTGRESQL",
    nên factory gom về một tên chuẩn để tránh nhân đôi nhánh xử lý.
    """

    if not isinstance(source_type, str) or not source_type.strip():
        raise ValueError(
            f"source_type không hợp lệ. Supported types: {', '.join(SUPPORTED_SOURCE_TYPES)}"
        )

    normalized = source_type.strip().upper()

    if normalized == "POSTGRES":
        return "POSTGRESQL"

    return normalized
