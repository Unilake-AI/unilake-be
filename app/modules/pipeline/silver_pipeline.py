"""Luồng S2/S3/S4 → Silver tường minh có phiên bản; không gọi phép biến đổi Gold cũ."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.modules.ingestion.contracts import BronzeBatch
    from app.modules.pipeline.identity_standardization import IdentityStandardizationService
    from app.modules.pipeline.schema_mapping import SchemaMappingService
    from app.modules.pipeline.silver_publication import PublicationReceipt, SilverPublicationService


@dataclass(frozen=True)
class NormalizationTask:
    batch: BronzeBatch
    mapping: SchemaMappingService
    identity: IdentityStandardizationService
    derivation_inputs: Mapping[str, Mapping[str, object]] | None = None
    derivation_input_version: str | None = None


class SilverPipeline:
    def __init__(self, publisher: SilverPublicationService) -> None:
        self.publisher = publisher

    def run(
        self, tasks: Sequence[NormalizationTask], *, base_snapshot: str | None = None
    ) -> PublicationReceipt:
        results = []
        for task in tasks:
            mapped = task.mapping.map_batch(task.batch)
            typed = task.identity.types.standardize(
                mapped,
                derivation_inputs=task.derivation_inputs,
                derivation_input_version=task.derivation_input_version,
            )
            results.append(task.identity.standardize(typed))
        return self.publisher.publish(results, base_snapshot=base_snapshot)
