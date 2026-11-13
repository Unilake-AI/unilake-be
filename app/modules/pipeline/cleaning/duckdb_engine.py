"""Làm sạch bằng DuckDB, giữ đúng quy tắc, vị trí dòng và kết quả của luồng trước."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from app.modules.ingestion.contracts import FileProvenance
from app.modules.ingestion.contracts.readers import (
    ReadRequest,
    ReadResult,
    SourceReaderFactory,
    read_file_snapshot,
)
from app.modules.pipeline.cleaning.contracts import (
    CleaningReport,
    CleaningReportSink,
    CleaningResult,
    CleaningTask,
    _is_s3_object,
    _valid_sha256,
    _validate_batch,
)
from app.modules.pipeline.cleaning.duplicate_records import (
    DuplicateIssue,
    DuplicateKeyRule,
    DuplicateProfile,
    DuplicateRecord,
    DuplicateResult,
    _is_incomplete_key_value,
    _same_key_group,
)
from app.modules.pipeline.cleaning.duplicate_records import (
    _validate_profile as _validate_duplicate_profile,
)
from app.modules.pipeline.cleaning.format_validation import (
    FormatFieldRule,
    FormatIssue,
    FormatProfile,
    FormatRecord,
    FormatResult,
    _InvalidFieldValueError,
    _normalize_and_validate,
)
from app.modules.pipeline.cleaning.format_validation import (
    _validate_profile as _validate_format_profile,
)
from app.modules.pipeline.cleaning.missing_values import (
    CleanedRecord as MissingCleanedRecord,
)
from app.modules.pipeline.cleaning.missing_values import (
    MissingValueIssue,
    MissingValueProfile,
    MissingValueResult,
)
from app.modules.pipeline.cleaning.missing_values import (
    QuarantinedRecord as MissingQuarantinedRecord,
)
from app.modules.pipeline.cleaning.missing_values import (
    _validate_context as _validate_missing_context,
)
from app.modules.pipeline.cleaning.missing_values import (
    _validate_profile as _validate_missing_profile,
)
from app.providers import cleaning_connection

if TYPE_CHECKING:
    from app.modules.pipeline.contracts.cleaning import CleaningConnection


def _format_check(
    rows: list[tuple[object, ...]],
    position: int,
    rule: FormatFieldRule,
    outcomes: dict[int, tuple[object, tuple[str, ...], tuple[str, str] | None]],
) -> Callable[[int], str]:
    """Giữ nguyên cách kiểm tra định dạng, kể cả giá trị và quy tắc lỗi chi tiết."""

    def check(index: int) -> str:
        value = rows[index][position]
        if value is None or value == "":
            outcomes[index] = (value, (), None)
            return ""
        try:
            normalized, changes = _normalize_and_validate(value, rule)
            outcomes[index] = (normalized, changes, None)
            return ""
        except _InvalidFieldValueError as exc:
            outcomes[index] = (value, (), (exc.code, exc.rule_id))
            return exc.code

    return check


class DuckDBCleaningEngine:
    """Giữ nguyên dữ liệu gốc khi kiểm tra, chỉ sửa theo quy định đã chọn.

    DuckDB tìm ô thiếu, nhóm khóa trùng và chọn dòng lỗi. Các quy định định dạng
    được gọi qua hàm kiểm tra hiện có để không thay đổi cách hiểu dữ liệu.
    """

    def __init__(
        self,
        issue_sink: CleaningReportSink | None = None,
        *,
        threads: int = 4,
        memory_limit: str = "2GB",
    ) -> None:
        self._issue_sink = issue_sink
        self._threads = threads
        self._memory_limit = memory_limit

    def connect(self) -> CleaningConnection:
        """Mở bộ nhớ riêng cho lần chạy, không dùng chung dữ liệu với yêu cầu khác."""
        return cleaning_connection(threads=self._threads, memory_limit=self._memory_limit)

    def read_csv_file(
        self,
        file_path: str | Path,
        *,
        delimiter: str = ",",
        has_header: bool = True,
        encoding: str = "utf-8",
        quote_char: str | None = None,
        skip_rows: int = 0,
    ) -> tuple[list[str], list[tuple[object, ...]], tuple[str, ...]]:
        """Dùng chung cách đọc CSV với bước tiếp nhận để không lệch cấu hình đọc."""
        result = SourceReaderFactory.get_reader("CSV").read(
            ReadRequest(
                "CSV",
                str(file_path),
                {
                    "delimiter": delimiter,
                    "has_header": has_header,
                    "encoding": encoding,
                    "quote_char": quote_char,
                    "skip_rows": skip_rows,
                },
            )
        )
        return (
            result.columns,
            result.rows,
            tuple(f"record:{index + 1}" for index in range(len(result.rows))),
        )

    def read_source(
        self,
        request: ReadRequest,
        *,
        source_system_id: str,
        data_source_id: str,
        dataset_id: str,
        expected_sha256: str | None = None,
        expected_rows: int | None = None,
    ) -> ReadResult:
        """Đọc đúng phiên bản tệp và đối chiếu dấu kiểm chứng, số dòng đã ghi nhận."""
        reader = SourceReaderFactory.get_reader(request.source_type)
        result = read_file_snapshot(
            request,
            reader,
            source_system_id=source_system_id,
            data_source_id=data_source_id,
            dataset_id=dataset_id,
        )
        provenance = result.provenance
        if provenance is None:
            raise ValueError("BRONZE_PROVENANCE_REQUIRED")
        if request.object_version_id and provenance.object_version_id != request.object_version_id:
            raise ValueError("BRONZE_OBJECT_VERSION_MISMATCH")
        if expected_sha256 and provenance.content_sha256.lower() != expected_sha256.lower():
            raise ValueError("BRONZE_OBJECT_CHECKSUM_MISMATCH")
        if expected_rows is not None and result.rows_read != expected_rows:
            raise ValueError("BRONZE_OBJECT_ROW_COUNT_MISMATCH")
        return result

    def load_table(
        self,
        conn: CleaningConnection,
        table_name: str,
        columns: list[str],
        rows: list[tuple[object, ...]],
        locators: tuple[str, ...],
    ) -> None:
        """Nạp theo lô bằng tên cột riêng; không dùng tên trong tệp làm tên cột tạm."""
        fields = [("_row_idx", "BIGINT"), ("_locator", "VARCHAR")]
        for position in range(len(columns)):
            fields.extend([(f"c{position}", "VARCHAR"), (f"n{position}", "BOOLEAN")])
        data: list[tuple[object, ...]] = []
        for index, (locator, row) in enumerate(zip(locators, rows, strict=True)):
            values: list[object] = [index, locator]
            for value in row:
                # Chỉ chuỗi mới được so với dấu hiệu ô trống. Số 7 không thành chuỗi "7".
                values.extend([value if isinstance(value, str) else None, value is None])
            data.append(tuple(values))
        conn.replace_table(table_name, fields, data)

    def _missing_matches(
        self,
        conn: CleaningConnection,
        columns: list[str],
        profile: MissingValueProfile,
    ) -> list[set[int]]:
        # Dùng đúng tập khoảng trắng của Python để khớp cách kiểm tra trước đây,
        # bao gồm cả dấu cách đặc biệt thường gặp khi sao chép từ Excel.
        whitespace = "".join(chr(code) for code in range(0x3100) if chr(code).isspace())
        matches: list[set[int]] = []
        for rule in profile.rules:
            if rule.field not in columns:
                matches.append(set())
                continue
            position = columns.index(rule.field)
            text = f"c{position}"
            parameters: list[object] = []
            if rule.trim_for_token_match:
                text = f"trim({text}, ?)"
                parameters.append(whitespace)
            if rule.null_tokens:
                tokens = ", ".join("?" for _ in rule.null_tokens)
                condition = f"COALESCE({text} IN ({tokens}), FALSE)"
                parameters.extend(rule.null_tokens)
            else:
                condition = "FALSE"
                parameters = []
            found = conn.execute(
                f"SELECT _row_idx FROM missing_stage WHERE n{position} OR {condition} "
                "ORDER BY _row_idx",
                parameters,
            ).fetchall()
            matches.append({index for (index,) in found})
        return matches

    def process_missing_values(
        self,
        *,
        columns: list[str],
        rows: list[tuple[object, ...]],
        locators: tuple[str, ...],
        profile: MissingValueProfile,
        batch_id: str,
        provenance: FileProvenance,
        conn: CleaningConnection | None = None,
    ) -> MissingValueResult:
        """Dùng DuckDB tìm các ô thiếu theo quy định của từng cột."""
        _validate_missing_profile(profile)
        if not isinstance(batch_id, str) or not batch_id.strip():
            raise ValueError("INVALID_BATCH_ID")
        if not isinstance(provenance, FileProvenance):
            raise ValueError("BRONZE_PROVENANCE_REQUIRED")
        if not columns or any(
            not isinstance(column, str) or not column.strip() for column in columns
        ):
            raise ValueError("INVALID_SOURCE_COLUMNS")
        if len(columns) != len(set(columns)):
            raise ValueError("DUPLICATE_SOURCE_COLUMNS")
        if any(len(row) != len(columns) for row in rows):
            raise ValueError("SOURCE_ROW_WIDTH_MISMATCH")
        if (
            len(locators) != len(rows)
            or any(not isinstance(locator, str) or not locator.strip() for locator in locators)
            or len(set(locators)) != len(locators)
        ):
            raise ValueError("INVALID_SOURCE_LOCATORS")

        missing_columns = set(profile.required_source_columns) - set(columns)
        missing_columns.update(
            rule.field
            for rule in profile.rules
            if rule.field not in columns and rule.required_in_source
        )
        if missing_columns:
            raise ValueError("REQUIRED_SOURCE_FIELD_MISSING:" + ",".join(sorted(missing_columns)))

        close_conn = False
        if conn is None:
            conn = self.connect()
            close_conn = True

        try:
            self.load_table(conn, "missing_stage", columns, rows, locators)

            matches = self._missing_matches(conn, columns, profile)
            accepted: list[MissingCleanedRecord] = []
            quarantined: list[MissingQuarantinedRecord] = []
            issues: list[MissingValueIssue] = []
            missing_counts: Counter[str] = Counter()

            for row_index, (raw_row, locator) in enumerate(zip(rows, locators, strict=True)):
                original = dict(zip(columns, raw_row, strict=True))
                cleaned = dict(original)
                row_issues: list[MissingValueIssue] = []
                row_quarantined = False

                for rule_index, rule in enumerate(profile.rules):
                    if row_index not in matches[rule_index]:
                        continue

                    missing_counts[rule.field] += 1
                    if rule.nullable:
                        cleaned[rule.field] = None
                        row_issues.append(
                            MissingValueIssue(
                                rule_id=rule.rule_id,
                                rule_version=profile.version,
                                domain=profile.domain,
                                entity=profile.entity,
                                field=rule.field,
                                code="NULL_VALUE_PRESERVED",
                                severity="INFO",
                                disposition="PRESERVED",
                                locator=locator,
                            )
                        )
                    else:
                        row_quarantined = True
                        row_issues.append(
                            MissingValueIssue(
                                rule_id="CLN-NULL-03",
                                rule_version=profile.version,
                                domain=profile.domain,
                                entity=profile.entity,
                                field=rule.field,
                                code="REQUIRED_VALUE_MISSING",
                                severity="ERROR",
                                disposition="QUARANTINED",
                                locator=locator,
                            )
                        )

                issues.extend(row_issues)
                if row_quarantined:
                    quarantined.append(MissingQuarantinedRecord(locator, original))
                else:
                    accepted.append(MissingCleanedRecord(locator, cleaned))

            return MissingValueResult(
                domain=profile.domain,
                entity=profile.entity,
                batch_id=batch_id,
                records_read=len(rows),
                passed=tuple(accepted),
                quarantined=tuple(quarantined),
                issues=tuple(issues),
                missing_values_by_field=dict(missing_counts),
                provenance=provenance,
            )
        finally:
            if close_conn:
                conn.close()

    def process_format_validation(
        self,
        *,
        columns: list[str],
        rows: list[tuple[object, ...]],
        locators: tuple[str, ...],
        profile: FormatProfile,
        batch_id: str,
        provenance: FileProvenance,
        conn: CleaningConnection | None = None,
    ) -> FormatResult:
        """Kiểm tra và chuẩn hóa định dạng các cột theo quy tắc FormatProfile."""
        _validate_format_profile(profile)
        if not isinstance(batch_id, str) or not batch_id.strip():
            raise ValueError("INVALID_BATCH_ID")
        if not columns or any(
            not isinstance(column, str) or not column.strip() for column in columns
        ):
            raise ValueError("INVALID_SOURCE_COLUMNS")
        if len(columns) != len(set(columns)):
            raise ValueError("DUPLICATE_SOURCE_COLUMNS")
        if any(len(row) != len(columns) for row in rows):
            raise ValueError("SOURCE_ROW_WIDTH_MISMATCH")
        if (
            len(locators) != len(rows)
            or any(not isinstance(locator, str) or not locator.strip() for locator in locators)
            or len(set(locators)) != len(locators)
        ):
            raise ValueError("INVALID_SOURCE_LOCATORS")
        missing_columns = set(profile.required_source_columns) - set(columns)
        if missing_columns:
            raise ValueError("REQUIRED_SOURCE_FIELD_MISSING:" + ",".join(sorted(missing_columns)))

        close_conn = False
        if conn is None:
            conn = self.connect()
            close_conn = True

        try:
            # Dữ liệu gốc đã ở trong bộ nhớ; chỉ chuyển số thứ tự sang bước kiểm tra.
            # Hàm định dạng lấy đúng giá trị ban đầu, không phải bản đã ép sang chuỗi.
            conn.replace_table(
                "format_stage", [("_row_idx", "BIGINT")], [(index,) for index in range(len(rows))]
            )
            outcomes: list[dict[int, tuple[object, tuple[str, ...], tuple[str, str] | None]]] = []
            invalid_indices: set[int] = set()
            for rule_index, rule in enumerate(profile.rules):
                checked: dict[int, tuple[object, tuple[str, ...], tuple[str, str] | None]] = {}
                outcomes.append(checked)
                if rule.field not in columns:
                    continue
                position = columns.index(rule.field)
                # Hàm định dạng cũ vẫn quyết định giá trị hợp lệ và mã lỗi.
                # DuckDB gọi hàm theo cột và lưu kết quả trước khi chọn các dòng lỗi.
                callback = _format_check(rows, position, rule, checked)
                function_name = "format_rule_" + uuid4().hex
                conn.register_rule(function_name, callback)
                table = f"format_result_{rule_index}"
                conn.execute(
                    f"CREATE OR REPLACE TABLE {table} AS SELECT _row_idx, "
                    f"{function_name}(_row_idx) AS error FROM format_stage"
                )
                invalid_indices.update(
                    index
                    for (index,) in conn.execute(
                        f"SELECT _row_idx FROM {table} WHERE error <> '' ORDER BY _row_idx"
                    ).fetchall()
                )

            accepted: list[FormatRecord] = []
            quarantined: list[FormatRecord] = []
            issues: list[FormatIssue] = []
            normalized_counts: Counter[str] = Counter()
            invalid_counts: Counter[str] = Counter()

            for row_index, (row, locator) in enumerate(zip(rows, locators, strict=True)):
                original = dict(zip(columns, row, strict=True))
                cleaned = dict(original)
                row_issues: list[FormatIssue] = []
                row_normalization_issues: list[FormatIssue] = []
                normalized_fields: set[str] = set()
                for rule_index, rule in enumerate(profile.rules):
                    if rule.field not in original:
                        continue
                    normalized, change_codes, error = outcomes[rule_index][row_index]
                    if error is not None:
                        invalid_counts[rule.field] += 1
                        row_issues.append(
                            FormatIssue(
                                rule_id=error[1],
                                rule_version=profile.version,
                                domain=profile.domain,
                                entity=profile.entity,
                                field=rule.field,
                                code=error[0],
                                severity="ERROR",
                                disposition="QUARANTINED",
                                locator=locator,
                            )
                        )
                        continue
                    cleaned[rule.field] = normalized
                    if change_codes:
                        normalized_fields.add(rule.field)
                    for code in change_codes:
                        row_normalization_issues.append(
                            FormatIssue(
                                rule_id=rule.rule_id,
                                rule_version=profile.version,
                                domain=profile.domain,
                                entity=profile.entity,
                                field=rule.field,
                                code=code,
                                severity="INFO",
                                disposition="NORMALIZED",
                                locator=locator,
                            )
                        )

                if row_index in invalid_indices:
                    quarantined.append(FormatRecord(locator=locator, values=original))
                    issues.extend(row_issues)
                else:
                    accepted.append(FormatRecord(locator=locator, values=cleaned))
                    issues.extend(row_normalization_issues)
                    for field in normalized_fields:
                        normalized_counts[field] += 1

            return FormatResult(
                domain=profile.domain,
                entity=profile.entity,
                batch_id=batch_id,
                records_read=len(rows),
                passed=tuple(accepted),
                quarantined=tuple(quarantined),
                issues=tuple(issues),
                normalized_values_by_field=dict(normalized_counts),
                invalid_values_by_field=dict(invalid_counts),
                provenance=provenance,
            )
        finally:
            if close_conn:
                conn.close()

    def process_duplicates(
        self,
        *,
        columns: list[str],
        rows: list[tuple[object, ...]],
        locators: tuple[str, ...],
        profile: DuplicateProfile,
        batch_id: str,
        provenance: FileProvenance,
        conn: CleaningConnection | None = None,
    ) -> DuplicateResult:
        """Dùng DuckDB tìm nhóm trùng khóa, rồi trả kết quả theo cấu trúc hiện có."""
        _validate_duplicate_profile(profile)
        if not isinstance(batch_id, str) or not batch_id.strip():
            raise ValueError("INVALID_BATCH_ID")
        if not columns or any(
            not isinstance(column, str) or not column.strip() for column in columns
        ):
            raise ValueError("INVALID_SOURCE_COLUMNS")
        if len(columns) != len(set(columns)):
            raise ValueError("DUPLICATE_SOURCE_COLUMNS")
        if any(len(row) != len(columns) for row in rows):
            raise ValueError("SOURCE_ROW_WIDTH_MISMATCH")
        if (
            len(locators) != len(rows)
            or any(not isinstance(locator, str) or not locator.strip() for locator in locators)
            or len(set(locators)) != len(locators)
        ):
            raise ValueError("INVALID_SOURCE_LOCATORS")
        missing_columns = set(profile.required_source_columns) - set(columns)
        if missing_columns:
            raise ValueError("REQUIRED_SOURCE_FIELD_MISSING:" + ",".join(sorted(missing_columns)))

        records = tuple(dict(zip(columns, row, strict=True)) for row in rows)
        field_positions = {column: index for index, column in enumerate(columns)}

        close_conn = False
        if conn is None:
            conn = self.connect()
            close_conn = True

        try:
            # Gán mã nhóm cho các giá trị bằng nhau theo đúng cách so sánh cũ.
            # Đây chỉ là cách biểu diễn khóa; DuckDB vẫn thực hiện phân nhóm bản ghi.
            # Số 7 và chuỗi "7" khác nhau, còn "007" được giữ riêng hoàn toàn.
            key_fields = tuple(
                dict.fromkeys(field for rule in profile.key_rules for field in rule.fields)
            )
            classes: list[dict[object, int]] = [{} for _ in key_fields]
            key_rows: list[tuple[object, ...]] = []
            for row_index, row in enumerate(rows):
                keys: list[object] = [row_index]
                for index, field in enumerate(key_fields):
                    value = row[field_positions[field]]
                    keys.append(
                        None
                        if _is_incomplete_key_value(value)
                        else classes[index].setdefault(value, len(classes[index]))
                    )
                key_rows.append(tuple(keys))
            conn.replace_table(
                "duplicate_stage",
                [
                    ("_row_idx", "BIGINT"),
                    *((f"k{index}", "BIGINT") for index in range(len(key_fields))),
                ],
                key_rows,
            )

            duplicate_groups: dict[int, list[tuple[DuplicateKeyRule, tuple[int, ...]]]] = (
                defaultdict(list)
            )
            groups_by_rule: dict[str, int] = {}
            rows_by_rule: dict[str, int] = {}

            # Lấy các nhóm trùng theo thứ tự xuất hiện để báo đúng dòng liên quan.
            for rule_index, rule in enumerate(profile.key_rules):
                quoted_rule_fields = ", ".join(
                    f"k{key_fields.index(field)}" for field in rule.fields
                )
                not_null_conds = " AND ".join(
                    f"k{key_fields.index(field)} IS NOT NULL" for field in rule.fields
                )
                sql = (
                    f"SELECT {quoted_rule_fields}, "
                    f"COUNT(*) as cnt, "
                    f"LIST(_row_idx ORDER BY _row_idx) as indices "
                    f"FROM duplicate_stage "
                    f"WHERE {not_null_conds} "
                    f"GROUP BY {quoted_rule_fields} "
                    f"HAVING COUNT(*) > 1 "
                    f"ORDER BY MIN(_row_idx)"
                )
                results = conn.execute(sql).fetchall()
                collisions = [tuple(row_item[-1]) for row_item in results]
                groups_by_rule[rule.rule_id] = len(collisions)
                rows_by_rule[rule.rule_id] = sum(len(indices) for indices in collisions)
                for indices in collisions:
                    duplicate_groups[rule_index].append((rule, indices))

            quarantined_indices: set[int] = set()
            collapse_groups: list[tuple[DuplicateKeyRule, tuple[int, ...]]] = []
            issues: list[DuplicateIssue] = []
            primary_rule = next(rule for rule in profile.key_rules if rule.kind == "PRIMARY_KEY")

            for rule, indices in (
                group for rule_groups in duplicate_groups.values() for group in rule_groups
            ):
                identical_payload = all(
                    records[index] == records[indices[0]] for index in indices[1:]
                )
                same_primary_key = _same_key_group(
                    indices, rows, field_positions, primary_rule.fields
                )
                if (
                    rule.kind == "PRIMARY_KEY"
                    and identical_payload
                    and profile.collapse_exact_primary_key_duplicates
                ):
                    collapse_groups.append((rule, indices))
                    continue
                if (
                    rule.kind != "PRIMARY_KEY"
                    and identical_payload
                    and same_primary_key
                    and profile.collapse_exact_primary_key_duplicates
                ):
                    continue

                quarantined_indices.update(indices)
                code = (
                    "DUPLICATE_PRIMARY_KEY_CONFLICT"
                    if rule.kind == "PRIMARY_KEY" and not identical_payload
                    else "DUPLICATE_KEY_COLLISION"
                )
                for index in indices:
                    related_index = next(other for other in indices if other != index)
                    issues.append(
                        DuplicateIssue(
                            rule_id=rule.rule_id,
                            rule_version=profile.version,
                            domain=profile.domain,
                            entity=profile.entity,
                            code=code,
                            disposition="QUARANTINED",
                            locator=locators[index],
                            related_locator=locators[related_index],
                            key_fields=rule.fields,
                        )
                    )

            collapsed_indices: set[int] = set()
            collapsed_by_primary: dict[int, tuple[str, ...]] = {}
            for rule, indices in collapse_groups:
                if any(index in quarantined_indices for index in indices):
                    quarantined_indices.update(indices)
                    continue
                winner = indices[0]
                collapsed_indices.update(indices[1:])
                collapsed_by_primary[winner] = tuple(locators[index] for index in indices)
                for index in indices[1:]:
                    issues.append(
                        DuplicateIssue(
                            rule_id=rule.rule_id,
                            rule_version=profile.version,
                            domain=profile.domain,
                            entity=profile.entity,
                            code="EXACT_DUPLICATE_COLLAPSED",
                            disposition="COLLAPSED",
                            locator=locators[index],
                            related_locator=locators[winner],
                            key_fields=rule.fields,
                        )
                    )

            passed = tuple(
                DuplicateRecord(
                    locator=locators[index],
                    values=records[index],
                    source_locators=collapsed_by_primary.get(index, (locators[index],)),
                )
                for index in range(len(records))
                if index not in quarantined_indices and index not in collapsed_indices
            )
            quarantined = tuple(
                DuplicateRecord(
                    locator=locators[index],
                    values=records[index],
                    source_locators=(locators[index],),
                )
                for index in range(len(records))
                if index in quarantined_indices
            )
            return DuplicateResult(
                domain=profile.domain,
                entity=profile.entity,
                batch_id=batch_id,
                records_read=len(rows),
                passed=passed,
                quarantined=quarantined,
                issues=tuple(issues),
                duplicate_groups_by_rule=dict(groups_by_rule),
                duplicate_records_by_rule=dict(rows_by_rule),
                provenance=provenance,
            )
        finally:
            if close_conn:
                conn.close()

    def clean(self, task: CleaningTask) -> CleaningResult:
        """Làm sạch Bronze, giữ phiên bản quy định, vị trí dòng, số dòng và mã lỗi."""
        batch = task.batch
        columns = _validate_batch(task)
        binding = batch.mapping_binding
        if binding is None:
            raise ValueError("MAPPING_BINDING_REQUIRED")
        rows = [tuple(record[column] for column in columns) for record in batch.records]
        provenance = batch.provenance
        if provenance is None:
            raise ValueError("BRONZE_PROVENANCE_REQUIRED")
        if not isinstance(batch.metadata, Mapping):
            raise ValueError("BRONZE_METADATA_REQUIRED")
        input_version = batch.metadata.get("input_version")
        if not isinstance(input_version, str) or not input_version.strip():
            raise ValueError("BRONZE_INPUT_VERSION_REQUIRED")
        if input_version != provenance.input_version:
            raise ValueError("BRONZE_INPUT_VERSION_MISMATCH")
        if (
            provenance.source_type != batch.source_type
            or provenance.source_system_id != binding.source_system_id
        ):
            raise ValueError("BRONZE_PROVENANCE_MISMATCH")
        if provenance.object_version_id is None or not provenance.object_version_id.strip():
            raise ValueError("BRONZE_OBJECT_VERSION_REQUIRED")
        if not _is_s3_object(provenance.source_ref):
            raise ValueError("BRONZE_OBJECT_URI_REQUIRED")
        if not _valid_sha256(provenance.content_sha256):
            raise ValueError("BRONZE_OBJECT_CHECKSUM_REQUIRED")

        conn = self.connect()
        try:
            # Bước 1: Tìm ô thiếu, giữ ô được phép trống và tách dòng thiếu dữ liệu bắt buộc.
            missing = self.process_missing_values(
                columns=columns,
                rows=rows,
                locators=batch.record_locators,
                profile=task.missing_profile,
                batch_id=batch.batch_id,
                provenance=provenance,
                conn=conn,
            )
            passed_after_missing = tuple(
                (record.locator, record.values) for record in missing.passed
            )
            quarantine_by_locator = {
                record.locator: record.values for record in missing.quarantined
            }

            # Bước 2: Kiểm tra và sửa định dạng theo quy định đã chọn.
            formatted = self.process_format_validation(
                columns=columns,
                rows=[
                    tuple(values[column] for column in columns)
                    for _, values in passed_after_missing
                ],
                locators=tuple(locator for locator, _ in passed_after_missing),
                profile=task.format_profile,
                batch_id=batch.batch_id,
                provenance=provenance,
                conn=conn,
            )
            for record in formatted.quarantined:
                quarantine_by_locator[record.locator] = record.values

            # Bước 3: Tìm trùng khóa, giữ thứ tự dòng và lịch sử gộp bản ghi.
            duplicates = self.process_duplicates(
                columns=columns,
                rows=[
                    tuple(record.values[column] for column in columns)
                    for record in formatted.passed
                ],
                locators=tuple(record.locator for record in formatted.passed),
                profile=task.duplicate_profile,
                batch_id=batch.batch_id,
                provenance=provenance,
                conn=conn,
            )
            for duplicate_record in duplicates.quarantined:
                quarantine_by_locator[duplicate_record.locator] = duplicate_record.values

            cleaned_batch = replace(
                batch,
                processing_steps=(
                    *batch.processing_steps,
                    ("CLEAN_MISSING", task.missing_profile.version),
                    ("CLEAN_FORMAT", task.format_profile.version),
                    ("CLEAN_DUPLICATES", task.duplicate_profile.version),
                ),
                records=tuple(dict(record.values) for record in duplicates.passed),
                record_locators=tuple(record.locator for record in duplicates.passed),
            )
            report = CleaningReport(
                domain=batch.domain_id,
                entity=binding.entity,
                batch_id=batch.batch_id,
                input_version=input_version,
                records_read=len(batch.records),
                records_passed=len(cleaned_batch.records),
                records_quarantined=len(quarantine_by_locator),
                records_collapsed=len(
                    {
                        issue.locator
                        for issue in duplicates.issues
                        if issue.disposition == "COLLAPSED"
                    }
                ),
                missing_issues=missing.issues,
                format_issues=formatted.issues,
                duplicate_issues=duplicates.issues,
            )
            if report.records_read != (
                report.records_passed + report.records_quarantined + report.records_collapsed
            ):
                raise ValueError("CLEANING_RECORD_COUNT_INVARIANT_FAILED")
            if self._issue_sink is not None:
                self._issue_sink.publish(report)
            return CleaningResult(cleaned_batch, report, tuple(quarantine_by_locator))
        finally:
            conn.close()

    def clean_bronze_object(
        self,
        request: ReadRequest,
        profile: MissingValueProfile,
        *,
        source_system_id: str,
        data_source_id: str,
        dataset_id: str,
        batch_id: str,
        expected_sha256: str,
        expected_rows: int,
    ) -> MissingValueResult:
        """Đọc đúng bản Bronze đã lưu và kiểm tra dữ liệu thiếu bằng DuckDB."""
        _validate_missing_context(
            request,
            profile,
            source_system_id=source_system_id,
            data_source_id=data_source_id,
            dataset_id=dataset_id,
            batch_id=batch_id,
            expected_sha256=expected_sha256,
            expected_rows=expected_rows,
        )
        read_result = self.read_source(
            request,
            source_system_id=source_system_id,
            data_source_id=data_source_id,
            dataset_id=dataset_id,
            expected_sha256=expected_sha256,
            expected_rows=expected_rows,
        )
        assert read_result.provenance is not None
        return self.process_missing_values(
            columns=read_result.columns,
            rows=read_result.rows,
            locators=read_result.record_locators,
            profile=profile,
            batch_id=batch_id,
            provenance=read_result.provenance,
        )
