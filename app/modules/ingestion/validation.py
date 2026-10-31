"""Contract validation đầu vào Bronze; chưa thực hiện I/O hoặc sửa dữ liệu nguồn."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

Name = Annotated[str, Field(min_length=1, max_length=200, pattern=r"^\S(?:.*\S)?$")]
Positive = Annotated[int, Field(gt=0)]
Count = Annotated[int, Field(ge=0)]


class FrozenContract(BaseModel):
    # Cấm trường ngoài contract để không vô tình nhận password/exception nguyên văn.
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class CSVOptions(FrozenContract):
    kind: Literal["CSV"] = "CSV"
    delimiter: str = Field(min_length=1, max_length=1)
    encoding: Name
    has_header: bool
    quote_char: str = Field(default='"', min_length=1, max_length=1)
    skip_rows: Count = 0
    positional_columns: tuple[Name, ...] = ()

    @model_validator(mode="after")
    def validate_options(self) -> Self:
        if self.delimiter in {"\n", "\r", "\x00", self.quote_char}:
            raise ValueError("INVALID_CSV_DELIMITER")
        if self.quote_char in {"\n", "\r", "\x00"}:
            raise ValueError("INVALID_CSV_QUOTE")
        if self.has_header and self.positional_columns:
            raise ValueError("POSITIONAL_COLUMNS_WITH_HEADER")
        if not self.has_header and not self.positional_columns:
            raise ValueError("POSITIONAL_COLUMNS_REQUIRED")
        if len(set(self.positional_columns)) != len(self.positional_columns):
            raise ValueError("DUPLICATE_POSITIONAL_COLUMNS")
        return self


class ExcelOptions(FrozenContract):
    kind: Literal["EXCEL"] = "EXCEL"
    sheet_name: Name
    header_row: Positive = 1
    skip_rows: Count = 0
    formula_policy: Literal["reject"] = "reject"


class PostgreSQLOptions(FrozenContract):
    kind: Literal["POSTGRESQL"] = "POSTGRESQL"
    schema_name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    table_name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    # Chỉ tham chiếu cấu hình kết nối; không đưa credential vào policy/report.
    connection_config_version: Name


ParserOptions = Annotated[
    CSVOptions | ExcelOptions | PostgreSQLOptions, Field(discriminator="kind")
]


class ResourceLimits(FrozenContract):
    max_file_bytes: Positive
    max_columns: Positive
    max_records: Positive
    max_cell_chars: Positive
    timeout_seconds: Positive
    memory_limit_bytes: Positive


class SourceColumn(FrozenContract):
    name: Name
    required: bool = True
    allowed_source_types: tuple[Name, ...] = ()


class ValidationPolicy(FrozenContract):
    policy_id: Name
    version: Name
    source_system_id: Positive
    data_source_id: Positive
    dataset_id: Positive
    parser: ParserOptions
    columns: tuple[SourceColumn, ...] = Field(min_length=1)
    column_order: Literal["any", "strict"] = "any"
    allow_extra_columns: bool = False
    allow_empty_records: bool = False
    limits: ResourceLimits

    @model_validator(mode="after")
    def validate_policy(self) -> Self:
        names = tuple(column.name for column in self.columns)
        if len(set(names)) != len(names):
            raise ValueError("DUPLICATE_SOURCE_COLUMNS")
        if len(names) > self.limits.max_columns:
            raise ValueError("CONTRACT_EXCEEDS_COLUMN_LIMIT")
        if isinstance(self.parser, CSVOptions) and not self.parser.has_header:
            if self.parser.positional_columns != names:
                raise ValueError("POSITIONAL_CONTRACT_MISMATCH")
            if self.allow_extra_columns or any(not c.required for c in self.columns):
                raise ValueError("POSITIONAL_CONTRACT_MUST_BE_EXACT")
        if isinstance(self.parser, PostgreSQLOptions) and any(
            not column.allowed_source_types for column in self.columns
        ):
            raise ValueError("POSTGRES_SOURCE_TYPES_REQUIRED")
        return self

    @property
    def fingerprint(self) -> str:
        # Dấu vân tay bao gồm binding, parser, rule và giới hạn tài nguyên.
        payload = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )
        return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


# Danh mục mã đóng: không nhận chuỗi exception hoặc giá trị dữ liệu làm mã lỗi.
ErrorCode = Literal[
    "SOURCE_NOT_FOUND",
    "SOURCE_ACCESS_DENIED",
    "SOURCE_UNAVAILABLE",
    "UNSUPPORTED_FILE_TYPE",
    "FILE_PARSE_ERROR",
    "ENCODING_ERROR",
    "HEADER_EMPTY",
    "HEADER_DUPLICATE",
    "SOURCE_SCHEMA_MISMATCH",
    "EXTRA_COLUMNS_ALLOWED",
    "EMPTY_SOURCE",
    "EMPTY_SOURCE_ALLOWED",
    "SHEET_NOT_FOUND",
    "EXCEL_FORMULA_UNSUPPORTED",
    "EXCEL_CELL_ERROR",
    "EXCEL_UNNAMED_COLUMN",
    "RESOURCE_LIMIT_EXCEEDED",
    "SOURCE_CHANGED",
    "VALIDATION_EXECUTION_ERROR",
]


class ValidationIssue(FrozenContract):
    rule_id: str = Field(pattern=r"^IN-(COM|CSV|XLS|PG)-\d{3}$")
    code: ErrorCode
    severity: Literal["WARNING", "ERROR"]
    # Không chứa raw value; row/column là tọa độ, không phải nội dung bản ghi.
    row_number: Positive | None = None
    column_number: Positive | None = None


class RuleCheck(FrozenContract):
    rule_id: str = Field(pattern=r"^IN-(COM|CSV|XLS|PG)-\d{3}$")
    required: bool = True
    status: Literal["PASS", "WARNING", "FAIL", "ERROR", "NOT_RUN"]


class ValidationReport(FrozenContract):
    report_id: Name
    ingestion_job_id: Positive
    attempt_id: Name
    policy_id: Name
    policy_version: Name
    policy_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_type: Literal["CSV", "EXCEL", "POSTGRESQL"]
    scan_complete: bool
    records_scanned: Count | None = None
    blank_rows_skipped: Count | None = None
    checks: tuple[RuleCheck, ...] = Field(min_length=1)
    issues: tuple[ValidationIssue, ...] = ()
    total_warnings: Count = 0
    total_errors: Count = 0

    @model_validator(mode="after")
    def validate_report(self) -> Self:
        checks = {check.rule_id: check for check in self.checks}
        if len(checks) != len(self.checks):
            raise ValueError("DUPLICATE_RULE_CHECK")
        if not any(check.required for check in self.checks):
            raise ValueError("REQUIRED_CHECKS_MISSING")
        if self.scan_complete and self.records_scanned is None:
            raise ValueError("COMPLETED_SCAN_COUNT_REQUIRED")
        for issue in self.issues:
            check = checks.get(issue.rule_id)
            allowed = {"WARNING"} if issue.severity == "WARNING" else {"FAIL", "ERROR"}
            if check is None or check.status not in allowed:
                raise ValueError("ISSUE_CHECK_MISMATCH")
        if sum(i.severity == "WARNING" for i in self.issues) > self.total_warnings:
            raise ValueError("WARNING_COUNT_MISMATCH")
        if sum(i.severity == "ERROR" for i in self.issues) > self.total_errors:
            raise ValueError("ERROR_COUNT_MISMATCH")
        has_warning = any(c.status == "WARNING" for c in self.checks)
        has_error = any(c.status in {"FAIL", "ERROR"} for c in self.checks)
        if has_warning != (self.total_warnings > 0) or has_error != (self.total_errors > 0):
            raise ValueError("CHECK_TOTAL_MISMATCH")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def result(self) -> Literal["PASS", "WARNING", "FAIL", "ERROR"]:
        # Không cho bên gọi tự gán PASS; lần quét dở dang luôn chặn tiếp nhận.
        if any(c.status == "ERROR" for c in self.checks):
            return "ERROR"
        if any(c.status == "FAIL" for c in self.checks):
            return "FAIL"
        if not self.scan_complete or any(c.required and c.status == "NOT_RUN" for c in self.checks):
            return "ERROR"
        return "WARNING" if self.total_warnings else "PASS"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def can_accept(self) -> bool:
        return self.result in {"PASS", "WARNING"}

    @computed_field  # type: ignore[prop-decorator]
    @property
    def issues_truncated(self) -> bool:
        return len(self.issues) < self.total_warnings + self.total_errors
