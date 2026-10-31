from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from app.modules.ingestion.contracts import FileProvenance
    from app.modules.ingestion.validation import ValidationPolicy


@dataclass
class ReadRequest:
    source_type: str
    location: str | None
    configuration: dict[str, Any]
    source_table: str | None = None
    object_version_id: str | None = None
    validation_policy: ValidationPolicy | None = None
    ingestion_job_id: int | None = None


@dataclass
class ReadResult:
    columns: list[str]
    rows: list[tuple[Any, ...]]
    source_table: str | None
    record_locators: tuple[str, ...] = ()
    provenance: FileProvenance | None = None
    workbook_epoch: str | None = None

    @property
    def rows_read(self) -> int:
        return len(self.rows)


class SourceReader(Protocol):
    def read(self, request: ReadRequest) -> ReadResult:
        """Đọc dữ liệu thô từ một nguồn và trả về theo định dạng chung.

        `SourceReader` chỉ là hợp đồng, chưa phải phần xử lý thật.
        Các reader cụ thể như CsvReader, ExcelReader hoặc PostgreSQLReader
        cần tự viết hàm này với cùng chữ ký hàm.

        Args:
            request: Thông tin đầu vào mà reader cần để đọc dữ liệu:
                - source_type: loại nguồn dữ liệu, ví dụ "csv" hoặc "postgresql".
                - location: đường dẫn file, URL hoặc địa chỉ kết nối nếu nguồn
                  dữ liệu cần.
                - configuration: cấu hình riêng của từng loại nguồn, ví dụ
                  delimiter, tên sheet, thông tin đăng nhập, câu query hoặc
                  encoding.
                - source_table: tên bảng/sheet tùy chọn, dùng để truy vết dữ
                  liệu đến từ đâu.

        Returns:
            ReadResult gồm danh sách tên cột, các dòng dữ liệu dạng tuple,
            và tên bảng/sheet nguồn để phục vụ truy vết.
        """
        ...
