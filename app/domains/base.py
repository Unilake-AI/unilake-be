"""Các hợp đồng ổn định mà mọi miền nghiệp vụ UniLake phải triển khai."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, TypeAlias

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

Record: TypeAlias = dict[str, object]


class DataLayer(StrEnum):
    """Các tầng data lake logic được cung cấp cho phần triển khai miền nghiệp vụ."""

    BRONZE = "bronze"
    SILVER = "silver"
    GOLD = "gold"


@dataclass(frozen=True)
class FieldDefinition:
    """Metadata trường dữ liệu dùng chung cho catalog và NLQ."""

    name: str
    data_type: str
    nullable: bool = True
    description: str = ""


@dataclass(frozen=True)
class DatasetDefinition:
    """Dataset logic thuộc một miền nghiệp vụ."""

    name: str
    layer: DataLayer
    fields: tuple[FieldDefinition, ...]
    description: str = ""
    primary_key: tuple[str, ...] = ()
    synonyms: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceMapping:
    """Ánh xạ có phiên bản từ cấu trúc nguồn sang dataset Silver."""

    source_type: str
    target_dataset: str
    version: str
    field_map: Mapping[str, str]


@dataclass(frozen=True)
class MetricDefinition:
    """Chỉ số nghiệp vụ được cung cấp cho phân tích và NLQ."""

    name: str
    dataset: str
    expression: str
    description: str = ""
    synonyms: tuple[str, ...] = ()


@dataclass(frozen=True)
class SemanticModel:
    """Metadata phục vụ NLQ cho tầng Gold đã công bố của miền nghiệp vụ."""

    metrics: tuple[MetricDefinition, ...] = ()


@dataclass(frozen=True)
class QualityResult:
    """Kết quả đánh giá một quy tắc chất lượng của miền nghiệp vụ."""

    rule_id: str
    passed: bool
    message: str
    failed_count: int = 0


class QualityRule(Protocol):
    """Quy tắc chất lượng gắn với một dataset đã khai báo của miền nghiệp vụ."""

    @property
    def rule_id(self) -> str:
        """Mã định danh ổn định của quy tắc."""
        ...

    @property
    def dataset(self) -> str:
        """Dataset được quy tắc này đánh giá."""
        ...

    @property
    def description(self) -> str:
        """Mô tả mục đích quy tắc để người đọc hiểu."""
        ...

    def evaluate(self, records: Sequence[Record]) -> QualityResult:
        """Đánh giá các bản ghi và trả về kết quả có thể tuần tự hóa."""
        ...


class DomainTransformer(Protocol):
    """Phép biến đổi thuộc miền nghiệp vụ được pipeline chung gọi thực thi."""

    def to_silver(
        self,
        records: Sequence[Record],
        mapping: SourceMapping,
    ) -> list[Record]:
        """Chuẩn hóa các bản ghi Bronze thành dataset Silver của ánh xạ."""
        ...

    def to_gold(self, records: Sequence[Record]) -> dict[str, list[Record]]:
        """Tạo các dataset Gold đã khai báo từ các bản ghi Silver đã kiểm tra."""
        ...


@dataclass(frozen=True)
class DomainDefinition:
    """Bản kê liên kết các hợp đồng miền nghiệp vụ có thể sử dụng độc lập."""

    domain_id: str
    display_name: str
    version: str
    schema_version: str
    datasets: tuple[DatasetDefinition, ...]
    mappings: tuple[SourceMapping, ...]
    transformer: DomainTransformer
    quality_rules: tuple[QualityRule, ...] = ()
    semantic_model: SemanticModel = field(default_factory=SemanticModel)

    def mapping_for(self, source_type: str) -> SourceMapping:
        """Trả về ánh xạ cho loại nguồn hoặc báo lỗi cụ thể."""
        matches = [mapping for mapping in self.mappings if mapping.source_type == source_type]
        if len(matches) != 1:
            raise LookupError(
                f"Domain '{self.domain_id}' has no unique mapping for source '{source_type}'"
            )
        return matches[0]

    def datasets_for(self, layer: DataLayer) -> tuple[DatasetDefinition, ...]:
        """Trả về các dataset đã khai báo cho một tầng logic."""
        return tuple(dataset for dataset in self.datasets if dataset.layer is layer)
