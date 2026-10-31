"""Điều phối Bronze → Silver → Gold độc lập với miền nghiệp vụ."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from app.domains.base import DataLayer, QualityResult, Record
from app.domains.registry import get_domain_registry
from app.modules.pipeline.contracts import (
    PipelineEvent,
    PipelineRunResult,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from app.domains.base import QualityRule
    from app.domains.registry import DomainRegistry
    from app.modules.ingestion.contracts import BronzeBatch
    from app.modules.pipeline.contracts import LayerPublisher, PipelineEventSink


class PipelineQualityError(ValueError):
    """Phát sinh khi cổng chất lượng của miền nghiệp vụ từ chối dữ liệu đã biến đổi."""

    def __init__(self, results: Sequence[QualityResult]) -> None:
        self.results = tuple(results)
        failed = ", ".join(result.rule_id for result in results if not result.passed)
        super().__init__(f"Pipeline quality gate failed: {failed}")


class PipelineOrchestrator:
    """Bộ điều phối chung chỉ phụ thuộc các hợp đồng miền nghiệp vụ ổn định."""

    def __init__(
        self,
        publisher: LayerPublisher,
        *,
        event_sink: PipelineEventSink | None = None,
        registry: DomainRegistry | None = None,
    ) -> None:
        self._publisher = publisher
        self._event_sink = event_sink
        self._registry = registry or get_domain_registry()

    def run(self, batch: BronzeBatch) -> PipelineRunResult:
        """Biến đổi, kiểm tra, công bố và báo cáo một batch Bronze."""
        if batch.mapping_binding is not None:
            raise ValueError(
                "Versioned structural batches require S3/S4 validation before publishing; "
                "use SilverPipeline for the versioned S2/S3/S4 and Silver publication path"
            )
        definition = self._registry.get(batch.domain_id)
        mapping = definition.mapping_for(batch.source_type)
        silver_records = definition.transformer.to_silver(batch.records, mapping)
        silver = {mapping.target_dataset: silver_records}

        silver_quality = self._evaluate_quality(definition.quality_rules, silver)
        self._raise_on_failure(silver_quality)
        self._publisher.publish(batch.domain_id, DataLayer.SILVER, silver)

        gold = definition.transformer.to_gold(silver_records)
        declared_gold = {dataset.name for dataset in definition.datasets_for(DataLayer.GOLD)}
        unknown_gold = set(gold).difference(declared_gold)
        if unknown_gold:
            unknown = ", ".join(sorted(unknown_gold))
            raise ValueError(f"Transformer returned undeclared Gold datasets: {unknown}")

        gold_quality = self._evaluate_quality(definition.quality_rules, gold)
        quality_results = (*silver_quality, *gold_quality)
        self._raise_on_failure(gold_quality)
        self._publisher.publish(batch.domain_id, DataLayer.GOLD, gold)

        run_id = str(uuid4())
        result = PipelineRunResult(
            run_id=run_id,
            domain_id=batch.domain_id,
            silver=self._freeze_datasets(silver),
            gold=self._freeze_datasets(gold),
            quality_results=quality_results,
        )
        if self._event_sink is not None:
            self._event_sink.publish(
                PipelineEvent(
                    run_id=run_id,
                    domain_id=batch.domain_id,
                    source=f"bronze:{batch.batch_id}",
                    targets=tuple(
                        [f"silver:{name}" for name in silver] + [f"gold:{name}" for name in gold]
                    ),
                    quality_results=quality_results,
                )
            )
        return result

    @staticmethod
    def _evaluate_quality(
        rules: Sequence[QualityRule],
        datasets: Mapping[str, Sequence[Record]],
    ) -> tuple[QualityResult, ...]:
        results: list[QualityResult] = []
        for rule in rules:
            if rule.dataset in datasets:
                result = rule.evaluate(datasets[rule.dataset])
                if not isinstance(result, QualityResult):
                    raise TypeError("Quality rules must return QualityResult")
                results.append(result)
        return tuple(results)

    @staticmethod
    def _raise_on_failure(results: Sequence[QualityResult]) -> None:
        if any(not result.passed for result in results):
            raise PipelineQualityError(results)

    @staticmethod
    def _freeze_datasets(
        datasets: Mapping[str, Sequence[Record]],
    ) -> dict[str, tuple[Record, ...]]:
        return {
            name: tuple(dict(record) for record in records) for name, records in datasets.items()
        }
