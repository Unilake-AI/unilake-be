"""Chuẩn hóa cấu trúc, kiểu và định danh qua DuckDB, giữ nguyên quy định đã kiểm chứng."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import TYPE_CHECKING
from uuid import uuid4

from app.modules.pipeline.standardization.identity_standardization import IdentityIssue
from app.modules.pipeline.standardization.schema_mapping import MappingConfigurationError
from app.providers import cleaning_connection

if TYPE_CHECKING:
    from app.modules.ingestion.contracts import BronzeBatch
    from app.modules.pipeline.standardization.identity_standardization import (
        IdentityResult,
        IdentityStandardizationService,
    )
    from app.modules.pipeline.standardization.schema_mapping import (
        MappingResult,
        SchemaMappingService,
    )
    from app.modules.pipeline.standardization.type_standardization import TypeResult


class DuckDBStandardizationEngine:
    """SQL điều phối từng dòng và tìm xung đột; hàm Python giữ đúng cách hiểu giá trị nguồn."""

    def _rows(self, count: int, callback: Callable[[int], None]) -> None:
        connection = cleaning_connection(threads=1, memory_limit="2GB")
        errors: dict[int, Exception] = {}

        def evaluate(index: int) -> str:
            try:
                callback(index)
                return "OK"
            except Exception as exc:
                errors[index] = exc
                return "ERROR"

        try:
            name = "standardize_" + uuid4().hex
            connection.register_rule(name, evaluate)
            connection.replace_table(
                "source_rows", [("_row_idx", "BIGINT")], [(i,) for i in range(count)]
            )
            # Lưu kết quả để mỗi dòng chỉ được kiểm tra một lần, không tùy kế hoạch truy vấn.
            connection.execute(
                f"CREATE TABLE checked AS SELECT _row_idx,{name}(_row_idx) AS status FROM source_rows"
            )
            connection.execute("SELECT _row_idx,status FROM checked ORDER BY _row_idx").fetchall()
            if errors:
                raise errors[min(errors)]
        finally:
            connection.close()

    def map_batch(
        self, batch: BronzeBatch, mapping: SchemaMappingService, *, preview_limit: int | None = None
    ) -> MappingResult:
        if (
            len(batch.records) != len(batch.record_locators)
            or len(set(batch.record_locators)) != len(batch.records)
            or any(not loc.strip() for loc in batch.record_locators)
        ):
            raise MappingConfigurationError("INVALID_RECORD_LOCATORS")
        base = mapping.map_batch(
            replace(batch, records=(), record_locators=()), preview_limit=preview_limit
        )
        records = batch.records if preview_limit is None else batch.records[:preview_limit]
        results: dict[int, MappingResult] = {}

        def apply(index: int) -> None:
            results[index] = mapping.map_batch(
                replace(
                    batch,
                    records=(records[index],),
                    record_locators=(batch.record_locators[index],),
                ),
                preview_limit=preview_limit,
            )

        self._rows(len(records), apply)
        ordered = [results[i] for i in range(len(records))]
        return replace(
            base,
            accepted=tuple(row for result in ordered for row in result.accepted),
            rejected_locators=tuple(loc for result in ordered for loc in result.rejected_locators),
            issues=(
                *base.issues,
                *(
                    issue
                    for result in ordered
                    for issue in result.issues
                    if issue.locator is not None
                ),
            ),
            inspected_count=len(records),
            total_count=len(batch.records),
        )

    def types(
        self,
        mapped: MappingResult,
        identity: IdentityStandardizationService,
        *,
        derivation_inputs: Mapping[str, Mapping[str, object]] | None = None,
        derivation_input_version: str | None = None,
    ) -> TypeResult:
        base = identity.types.standardize(
            replace(mapped, accepted=()),
            derivation_inputs=derivation_inputs,
            derivation_input_version=derivation_input_version,
        )
        results: dict[int, TypeResult] = {}

        def apply(index: int) -> None:
            results[index] = identity.types.standardize(
                replace(
                    mapped,
                    accepted=(mapped.accepted[index],),
                    rejected_locators=(),
                    issues=(),
                    inspected_count=1,
                    total_count=1,
                ),
                derivation_inputs=derivation_inputs,
                derivation_input_version=derivation_input_version,
            )

        self._rows(len(mapped.accepted), apply)
        ordered = [results[i] for i in range(len(mapped.accepted))]
        return replace(
            base,
            accepted=tuple(row for result in ordered for row in result.accepted),
            rejected_locators=(
                *mapped.rejected_locators,
                *(loc for result in ordered for loc in result.rejected_locators),
            ),
            issues=tuple(issue for result in ordered for issue in result.issues),
        )

    def identities(
        self, typed: TypeResult, identity: IdentityStandardizationService
    ) -> IdentityResult:
        # Kiểm tra bàn giao toàn lô trước khi chia dòng; không bỏ qua mã trùng hoặc thiếu vị trí.
        locators = [r.locator for r in typed.accepted] + list(typed.rejected_locators)
        if (
            len(locators) != len(set(locators))
            or len(locators) != typed.inspected_count
            or typed.total_count < typed.inspected_count
            or (not typed.preview and typed.total_count != typed.inspected_count)
            or not typed.input_version
            or any(not loc.strip() for loc in locators)
        ):
            from app.modules.pipeline.contracts.identity import IdentityConfigurationError

            raise IdentityConfigurationError("INVALID_TYPE_HANDOFF")
        base = identity.standardize(
            replace(typed, accepted=(), rejected_locators=(), inspected_count=0, total_count=0)
        )
        results: dict[int, IdentityResult] = {}

        def apply(index: int) -> None:
            results[index] = identity.standardize(
                replace(
                    typed,
                    accepted=(typed.accepted[index],),
                    rejected_locators=(),
                    inspected_count=1,
                    total_count=1,
                )
            )

        self._rows(len(typed.accepted), apply)
        ordered = [results[i] for i in range(len(typed.accepted))]
        accepted = tuple(row for result in ordered for row in result.accepted)
        keys = tuple(
            name for name in identity.types.spec["x-primary-key"] if not name.startswith("_")
        )
        collisions: set[int] = set()
        if keys:
            connection = cleaning_connection(threads=1, memory_limit="2GB")
            try:
                # Dùng chuỗi cho mã nguồn để DuckDB không bỏ mất số 0 ở đầu.
                source_mode = identity.types.spec.get("x-identity-mode") == "source"
                key_type = "VARCHAR" if source_mode else "BIGINT"
                connection.replace_table(
                    "canonical_rows",
                    [("_row_idx", "BIGINT"), *((f"k{i}", key_type) for i in range(len(keys)))],
                    [(index, *(row.values[k] for k in keys)) for index, row in enumerate(accepted)],
                )
                fields = ",".join(f"k{i}" for i in range(len(keys)))
                groups = connection.execute(
                    f"SELECT LIST(_row_idx ORDER BY _row_idx) FROM canonical_rows GROUP BY {fields} HAVING COUNT(*)>1"
                ).fetchall()
                collisions = {index for (indices,) in groups for index in indices}
            finally:
                connection.close()
        return replace(
            base,
            accepted=tuple(row for i, row in enumerate(accepted) if i not in collisions),
            rejected_locators=(
                *typed.rejected_locators,
                *(loc for result in ordered for loc in result.rejected_locators),
                *(row.locator for i, row in enumerate(accepted) if i in collisions),
            ),
            issues=(
                *(issue for result in ordered for issue in result.issues),
                *(
                    IdentityIssue(
                        row.locator,
                        ",".join(keys),
                        "CANONICAL_KEY_COLLISION",
                        "error",
                        identity.version,
                    )
                    for i, row in enumerate(accepted)
                    if i in collisions
                ),
            ),
            inspected_count=typed.inspected_count,
            total_count=typed.total_count,
        )

    def standardize(
        self,
        batch: BronzeBatch,
        mapping: SchemaMappingService,
        identity: IdentityStandardizationService,
        *,
        derivation_inputs: Mapping[str, Mapping[str, object]] | None = None,
        derivation_input_version: str | None = None,
    ) -> IdentityResult:
        mapped = self.map_batch(batch, mapping)
        typed = self.types(
            mapped,
            identity,
            derivation_inputs=derivation_inputs,
            derivation_input_version=derivation_input_version,
        )
        return self.identities(typed, identity)
