"""Các dịch vụ catalog và quản trị dữ liệu khi thực thi dùng chung."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.domains.base import DataLayer, FieldDefinition  # noqa: TC001 - Pydantic cần lúc runtime
from app.domains.registry import get_domain_registry

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.domains.registry import DomainRegistry
    from app.modules.pipeline.contracts import PipelineEvent

from app.modules.governance.schemas import LineageEdge, LineageGraphResponse, LineageNode
from app.repositories.lineage_repo import LineageRepository


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

    async def get_lineage_graph(
        self,
        db: AsyncSession,
        target_id: str | None = None,
    ) -> LineageGraphResponse:
        """Truy vấn đồ thị Lineage từ database."""
        repo = LineageRepository(db)
        raw_edges = await repo.get_lineage_graph(target_id=target_id)

        nodes_dict: dict[str, LineageNode] = {}
        edges: list[LineageEdge] = []

        for row in raw_edges:
            src_key = f"{row['source_layer']}:{row['source_id']}"
            if src_key not in nodes_dict:
                nodes_dict[src_key] = LineageNode(
                    id=src_key,
                    label=row["source_name"],
                    layer=row["source_layer"],
                )

            tgt_key = f"{row['target_layer']}:{row['target_id']}"
            if tgt_key not in nodes_dict:
                nodes_dict[tgt_key] = LineageNode(
                    id=tgt_key,
                    label=row["target_name"],
                    layer=row["target_layer"],
                )

            edges.append(
                LineageEdge(
                    source=src_key,
                    target=tgt_key,
                    lineage_type=row["lineage_type"],
                    job_id=row["job_id"],
                    metadata=row.get("metadata", {}),
                )
            )

        return LineageGraphResponse(nodes=list(nodes_dict.values()), edges=edges)
