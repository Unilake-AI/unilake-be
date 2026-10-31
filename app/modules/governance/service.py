"""Các dịch vụ catalog và quản trị dữ liệu khi thực thi dùng chung."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.domains.base import DataLayer, FieldDefinition  # noqa: TC001 - Pydantic cần lúc runtime
from app.domains.registry import get_domain_registry

if TYPE_CHECKING:
    from app.domains.registry import DomainRegistry
    from app.modules.pipeline.contracts import PipelineEvent


@dataclass(frozen=True)
class CatalogEntry:
    """Biểu diễn catalog được tạo từ dataset miền nghiệp vụ đã đăng ký."""

    domain_id: str
    dataset: str
    layer: DataLayer
    schema_version: str
    description: str
    fields: tuple[FieldDefinition, ...]


@dataclass(frozen=True)
class QualityRuleEntry:
    """Metadata quy tắc chất lượng có thể tra cứu từ miền nghiệp vụ."""

    domain_id: str
    rule_id: str
    dataset: str
    description: str


class GovernanceService:
    """Sử dụng metadata miền nghiệp vụ và bằng chứng pipeline mà không rẽ nhánh theo miền."""

    def __init__(self, registry: DomainRegistry | None = None) -> None:
        self._registry = registry or get_domain_registry()
        self._events: list[PipelineEvent] = []

    def catalog(self, *, domain_id: str | None = None) -> tuple[CatalogEntry, ...]:
        """Chuyển các schema đã đăng ký thành các mục catalog dùng chung."""
        definitions = (
            (self._registry.get(domain_id),) if domain_id is not None else self._registry.all()
        )
        return tuple(
            CatalogEntry(
                domain_id=definition.domain_id,
                dataset=dataset.name,
                layer=dataset.layer,
                schema_version=definition.schema_version,
                description=dataset.description,
                fields=dataset.fields,
            )
            for definition in definitions
            for dataset in definition.datasets
        )

    def publish(self, event: PipelineEvent) -> None:
        """Lưu bằng chứng nguồn gốc và chất lượng dữ liệu do pipeline phát ra khi thực thi."""
        self._registry.get(event.domain_id)
        self._events.append(event)

    def quality_rules(self, *, domain_id: str | None = None) -> tuple[QualityRuleEntry, ...]:
        """Trả về metadata quy tắc chất lượng tĩnh từ các miền nghiệp vụ đã đăng ký."""
        definitions = (
            (self._registry.get(domain_id),) if domain_id is not None else self._registry.all()
        )
        return tuple(
            QualityRuleEntry(
                domain_id=definition.domain_id,
                rule_id=rule.rule_id,
                dataset=rule.dataset,
                description=rule.description,
            )
            for definition in definitions
            for rule in definition.quality_rules
        )

    def lineage(self, *, domain_id: str | None = None) -> tuple[PipelineEvent, ...]:
        """Trả về các sự kiện nguồn gốc dữ liệu đã ghi nhận khi thực thi."""
        return tuple(
            event for event in self._events if domain_id is None or event.domain_id == domain_id
        )
