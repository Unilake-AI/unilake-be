"""Ranh giới ingestion dùng chung từ nguồn đến Bronze."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from app.domains.registry import get_domain_registry
from app.modules.ingestion.contracts import BronzeBatch

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from app.domains.base import Record
    from app.domains.registry import DomainRegistry
    from app.modules.ingestion.contracts import BronzeRepository


class InMemoryBronzeRepository:
    """Kho lưu trữ có kết quả xác định cho kiểm thử và thử nghiệm cục bộ."""

    def __init__(self) -> None:
        self.batches: dict[str, BronzeBatch] = {}

    def write(self, batch: BronzeBatch) -> None:
        self.batches[batch.batch_id] = batch


def ingest_records(
    domain_id: str,
    source_type: str,
    records: Sequence[Record],
    repository: BronzeRepository,
    *,
    metadata: Mapping[str, str] | None = None,
    registry: DomainRegistry | None = None,
) -> BronzeBatch:
    """Kiểm tra metadata định tuyến và lưu bền vững các bản ghi thô vào Bronze."""
    active_registry = registry or get_domain_registry()
    domain = active_registry.get(domain_id)
    domain.mapping_for(source_type)

    batch = BronzeBatch(
        batch_id=str(uuid4()),
        domain_id=domain_id,
        source_type=source_type,
        records=tuple(dict(record) for record in records),
        metadata=dict(metadata or {}),
    )
    repository.write(batch)
    return batch
