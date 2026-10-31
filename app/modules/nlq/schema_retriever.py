"""Truy xuất schema để tạo ngữ cảnh NLQ."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.domains.base import DataLayer
from app.domains.registry import get_domain_registry

if TYPE_CHECKING:
    from collections.abc import Sequence

    from app.domains.registry import DomainRegistry


def retrieve_schema_context(
    intent: str,
    domain_ids: Sequence[str] | None = None,
    *,
    registry: DomainRegistry | None = None,
) -> str:
    """Tạo ngữ cảnh NLQ động từ metadata Gold đã đăng ký."""
    active_registry = registry or get_domain_registry()
    definitions = (
        tuple(active_registry.get(domain_id) for domain_id in domain_ids)
        if domain_ids is not None
        else active_registry.all()
    )

    lines = [f"Intent: {intent}"]
    for definition in definitions:
        lines.append(
            f"Domain: {definition.domain_id} ({definition.display_name}), "
            f"schema_version={definition.schema_version}"
        )
        for dataset in definition.datasets_for(DataLayer.GOLD):
            fields = ", ".join(
                f"{field.name}:{field.data_type}{'?' if field.nullable else ''}"
                for field in dataset.fields
            )
            lines.append(f"Gold dataset {dataset.name}: {fields}. {dataset.description}")
        for metric in definition.semantic_model.metrics:
            synonyms = ", ".join(metric.synonyms)
            lines.append(
                f"Metric {metric.name} on {metric.dataset}: {metric.expression}. "
                f"{metric.description} Synonyms: {synonyms}"
            )
    return "\n".join(lines)
