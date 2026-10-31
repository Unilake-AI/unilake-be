"""Kết quả pipeline dùng chung và các cổng ra."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from app.domains.base import DataLayer, QualityResult, Record


@dataclass(frozen=True)
class PipelineEvent:
    """Bằng chứng nguồn gốc và chất lượng dữ liệu được phát ra khi chạy pipeline."""

    run_id: str
    domain_id: str
    source: str
    targets: tuple[str, ...]
    quality_results: tuple[QualityResult, ...]


@dataclass(frozen=True)
class PipelineRunResult:
    """Kết quả đã công bố của một lần chạy Bronze → Silver → Gold."""

    run_id: str
    domain_id: str
    silver: Mapping[str, tuple[Record, ...]]
    gold: Mapping[str, tuple[Record, ...]]
    quality_results: tuple[QualityResult, ...]


class LayerPublisher(Protocol):
    """Cổng lưu trữ mà pipeline dùng để công bố dataset đã kiểm tra."""

    def publish(
        self,
        domain_id: str,
        layer: DataLayer,
        datasets: Mapping[str, Sequence[Record]],
    ) -> None:
        """Công bố các dataset cho một tầng logic theo cách nguyên tử."""


class PipelineEventSink(Protocol):
    """Cổng quản trị tiếp nhận bằng chứng thực thi pipeline."""

    def publish(self, event: PipelineEvent) -> None:
        """Ghi nhận một sự kiện pipeline thành công."""


class InMemoryLayerPublisher:
    """Bộ công bố tham chiếu cho kiểm thử và thử nghiệm cục bộ."""

    def __init__(self) -> None:
        self.datasets: dict[tuple[str, DataLayer, str], tuple[Record, ...]] = {}

    def publish(
        self,
        domain_id: str,
        layer: DataLayer,
        datasets: Mapping[str, Sequence[Record]],
    ) -> None:
        for name, records in datasets.items():
            self.datasets[(domain_id, layer, name)] = tuple(dict(record) for record in records)
