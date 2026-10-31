"""Các hợp đồng bàn giao công khai thuộc mô-đun ingestion."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime
    from pathlib import Path

    from app.domains.base import Record
    from app.modules.ingestion.validation import ValidationReport


@dataclass(frozen=True)
class CapturedArtifact:
    """Bản chụp dữ liệu thô riêng sẵn sàng để lưu bền vững vào Bronze."""

    path: Path
    rows: int
    sha256: str
    source_version_id: str | None = None
    validation_report: ValidationReport | None = None


@dataclass(frozen=True)
class BronzeWriteContext:
    """Thông tin định danh job đã kiểm tra cho bộ điều hợp lưu trữ, không kèm trạng thái ORM."""

    ingestion_job_id: int
    created_at: datetime
    # Dùng ID catalog ổn định, không đưa tên nguồn hoặc cấu hình kết nối vào object.
    source_system_id: int
    ingestion_timestamp: datetime


@dataclass(frozen=True)
class FileProvenance:
    """Bằng chứng cho một bản chụp tệp đã phân tích; không phải biên nhận lưu Bronze bền vững."""

    source_system_id: str
    data_source_id: str
    dataset_id: str
    source_type: str
    source_ref: str
    object_version_id: str | None
    content_sha256: str
    parser_fingerprint: str
    columns_fingerprint: str
    captured_at: str
    locator_kind: str
    workbook_epoch: str | None = None

    @property
    def input_version(self) -> str:
        return "sha256:" + self.content_sha256


@dataclass(frozen=True)
class MappingBinding:
    """Định danh nguồn/thực thể/profile được cố định để chuẩn hóa cấu trúc."""

    source_system_id: str
    entity: str
    source_schema_version: str
    mapping_id: str
    mapping_version: str
    target_schema_version: str


@dataclass(frozen=True)
class BronzeBatch:
    """Dữ liệu bàn giao bất biến từ ingestion cho các thành phần phía sau."""

    batch_id: str
    domain_id: str
    source_type: str
    records: tuple[Record, ...]
    metadata: Mapping[str, str] = field(default_factory=dict)
    mapping_binding: MappingBinding | None = None
    record_locators: tuple[str, ...] = ()
    source_paths: tuple[str, ...] = ()
    provenance: FileProvenance | None = None


class BronzeRepository(Protocol):
    """Cổng lưu bền vững các batch dữ liệu thô vào tầng Bronze."""

    def write(self, batch: BronzeBatch) -> None:
        """Lưu bền vững batch dữ liệu thô mà không áp dụng biến đổi nghiệp vụ."""
