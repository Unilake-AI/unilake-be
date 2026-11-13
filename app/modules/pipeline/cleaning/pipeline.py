"""Điều phối làm sạch dữ liệu Bronze bằng bộ xử lý đã chọn."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.modules.pipeline.cleaning.contracts import CleaningReport as CleaningReport
from app.modules.pipeline.cleaning.contracts import CleaningReportSink as CleaningReportSink
from app.modules.pipeline.cleaning.contracts import CleaningResult as CleaningResult
from app.modules.pipeline.cleaning.contracts import CleaningTask as CleaningTask
from app.modules.pipeline.cleaning.duckdb_engine import DuckDBCleaningEngine

__all__ = [
    "CleaningNormalizationTask",
    "CleaningPipeline",
    "CleaningPublicationResult",
    "CleaningReport",
    "CleaningReportSink",
    "CleaningResult",
    "CleaningSilverPipeline",
    "CleaningTask",
]

if TYPE_CHECKING:
    from app.modules.pipeline.silver.pipeline import SilverPipeline
    from app.modules.pipeline.silver.publication import PublicationReceipt
    from app.modules.pipeline.standardization.identity_standardization import (
        IdentityStandardizationService,
    )
    from app.modules.pipeline.standardization.schema_mapping import SchemaMappingService


@dataclass(frozen=True)
class CleaningNormalizationTask:
    cleaning: CleaningTask
    mapping: SchemaMappingService
    identity: IdentityStandardizationService
    derivation_inputs: Mapping[str, Mapping[str, object]] | None = None
    derivation_input_version: str | None = None


@dataclass(frozen=True)
class CleaningPublicationResult:
    publication: PublicationReceipt
    cleaning_reports: tuple[CleaningReport, ...]


class CleaningPipeline:
    """Xử lý dữ liệu thiếu, kiểm tra định dạng và xử lý bản ghi trùng trong dữ liệu Bronze."""

    def __init__(
        self,
        issue_sink: CleaningReportSink | None = None,
        *,
        engine: DuckDBCleaningEngine | None = None,
    ) -> None:
        # Phần điều phối chỉ gọi bộ xử lý; cấu trúc kết quả nằm ở tệp dùng chung.
        self._engine = engine or DuckDBCleaningEngine(issue_sink=issue_sink)

    def clean(self, task: CleaningTask) -> CleaningResult:
        return self._engine.clean(task)


class CleaningSilverPipeline:
    """Làm sạch dữ liệu đầu vào, chỉ chuyển các bản ghi đạt yêu cầu sang bước chuẩn hóa và lưu vào Silver."""

    def __init__(
        self,
        silver_pipeline: SilverPipeline,
        cleaning_pipeline: CleaningPipeline | None = None,
    ) -> None:
        self._silver_pipeline = silver_pipeline
        self._cleaning_pipeline = cleaning_pipeline or CleaningPipeline()

    def run(
        self,
        tasks: Sequence[CleaningNormalizationTask],
        *,
        base_snapshot: str | None = None,
    ) -> CleaningPublicationResult:
        from app.modules.pipeline.silver.pipeline import NormalizationTask

        normalization_tasks = []
        reports = []
        for task in tasks:
            cleaned = self._cleaning_pipeline.clean(task.cleaning)
            reports.append(cleaned.report)
            normalization_tasks.append(
                NormalizationTask(
                    batch=cleaned.batch,
                    mapping=task.mapping,
                    identity=task.identity,
                    derivation_inputs=task.derivation_inputs,
                    derivation_input_version=task.derivation_input_version,
                )
            )
        publication = self._silver_pipeline.run(
            tuple(normalization_tasks),
            base_snapshot=base_snapshot,
        )
        return CleaningPublicationResult(publication, tuple(reports))
