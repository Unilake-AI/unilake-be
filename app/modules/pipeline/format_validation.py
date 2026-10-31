"""Normalize configured Bronze field formats and quarantine invalid records."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Literal, assert_never
from urllib.parse import urlsplit

from app.modules.ingestion.readers.factory import SourceReaderFactory
from app.modules.ingestion.readers.file_snapshot import read_file_snapshot

if TYPE_CHECKING:
    from app.modules.ingestion.contracts import FileProvenance
    from app.modules.ingestion.readers.contract import ReadRequest

ValueKind = Literal["string", "integer", "decimal", "date", "timestamp", "boolean"]
IssueSeverity = Literal["INFO", "ERROR"]
IssueDisposition = Literal["NORMALIZED", "QUARANTINED"]


@dataclass(frozen=True)
class SourceFormatOptions:
    """Source-specific transformations; none are inferred from sample values."""

    trim: bool = False
    unicode_nfc: bool = False
    aliases: tuple[tuple[str, str], ...] = ()
    date_formats: tuple[str, ...] = ()
    timestamp_formats: tuple[str, ...] = ()
    decimal_separator: str = "."
    thousands_separator: str | None = None
    true_tokens: tuple[str, ...] = ("true",)
    false_tokens: tuple[str, ...] = ("false",)


@dataclass(frozen=True)
class FormatFieldRule:
    """Validation and normalization policy for one parsed source column."""

    field: str
    target_field: str
    value_kind: ValueKind
    rule_id: str
    options: SourceFormatOptions = dataclass_field(default_factory=SourceFormatOptions)
    required_in_source: bool = True
    pattern: str | None = None
    enum: tuple[str, ...] = ()
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    min_length: int | None = None
    max_length: int | None = None
    precision: int | None = None
    scale: int | None = None


@dataclass(frozen=True)
class FormatProfile:
    """Versioned format policy bound to one domain/entity and source layout."""

    domain: str
    entity: str
    version: str
    required_source_columns: tuple[str, ...]
    rules: tuple[FormatFieldRule, ...]


@dataclass(frozen=True)
class FormatRecord:
    locator: str
    values: Mapping[str, object]


@dataclass(frozen=True)
class FormatIssue:
    rule_id: str
    rule_version: str
    domain: str
    entity: str
    field: str
    code: str
    severity: IssueSeverity
    disposition: IssueDisposition
    locator: str


@dataclass(frozen=True)
class FormatResult:
    domain: str
    entity: str
    batch_id: str
    records_read: int
    passed: tuple[FormatRecord, ...]
    quarantined: tuple[FormatRecord, ...]
    issues: tuple[FormatIssue, ...]
    normalized_values_by_field: Mapping[str, int]
    invalid_values_by_field: Mapping[str, int]
    provenance: FileProvenance


class _InvalidFieldValueError(ValueError):
    def __init__(self, code: str, *, rule_id: str = "CLN-VAL-01") -> None:
        self.code = code
        self.rule_id = rule_id
        super().__init__(code)


def build_format_profile(
    schema: Mapping[str, object],
    *,
    domain: str,
    entity: str,
    version: str,
    target_to_source: Mapping[str, str],
    source_options: Mapping[str, SourceFormatOptions] | None = None,
    optional_source_fields: tuple[str, ...] = (),
) -> FormatProfile:
    """Derive field constraints from a domain schema and explicit source mapping."""
    entities = schema.get("entities")
    if not isinstance(entities, Mapping):
        raise ValueError("FORMAT_SCHEMA_ENTITIES_REQUIRED")
    entity_schema = entities.get(entity)
    if not isinstance(entity_schema, Mapping):
        raise ValueError("FORMAT_ENTITY_SCHEMA_REQUIRED")
    columns = entity_schema.get("columns")
    if not isinstance(columns, Sequence) or isinstance(columns, str):
        raise ValueError("FORMAT_SCHEMA_COLUMNS_INVALID")
    file_format = schema.get("file_format", {})
    if not isinstance(file_format, Mapping):
        raise ValueError("FORMAT_SCHEMA_FILE_FORMAT_INVALID")
    options_by_target = dict(source_options or {})
    if not isinstance(optional_source_fields, tuple) or any(
        not isinstance(field, str) or not field for field in optional_source_fields
    ):
        raise ValueError("INVALID_OPTIONAL_SOURCE_FIELDS")
    schema_fields = {
        column.get("name")
        for column in columns
        if isinstance(column, Mapping) and isinstance(column.get("name"), str)
    }
    if (
        set(target_to_source) - schema_fields
        or set(options_by_target) - set(target_to_source)
        or set(optional_source_fields) - set(target_to_source)
    ):
        raise ValueError("FORMAT_UNKNOWN_TARGET_FIELD")

    rules: list[FormatFieldRule] = []
    used_source_fields: set[str] = set()
    for column in columns:
        if not isinstance(column, Mapping):
            raise ValueError("FORMAT_SCHEMA_COLUMN_INVALID")
        target_field = column.get("name")
        if not isinstance(target_field, str) or not target_field.strip():
            raise ValueError("FORMAT_SCHEMA_COLUMN_NAME_REQUIRED")
        if target_field not in target_to_source:
            continue
        source_field = target_to_source[target_field]
        if not isinstance(source_field, str) or not source_field.strip():
            raise ValueError(f"FORMAT_SOURCE_MAPPING_REQUIRED:{target_field}")
        if source_field in used_source_fields:
            raise ValueError("FORMAT_SOURCE_MAPPING_NOT_UNIQUE")
        used_source_fields.add(source_field)
        value_kind, precision, scale, max_length = _schema_value_kind(column)
        options = options_by_target.get(target_field, SourceFormatOptions())
        if not isinstance(options, SourceFormatOptions):
            raise ValueError(f"INVALID_SOURCE_FORMAT_OPTIONS:{target_field}")
        date_formats = options.date_formats
        timestamp_formats = options.timestamp_formats
        if value_kind == "date" and not date_formats:
            date_format = file_format.get("date_format", "YYYY-MM-DD")
            date_formats = (_schema_date_format(date_format),)
            options = SourceFormatOptions(
                trim=options.trim,
                unicode_nfc=options.unicode_nfc,
                aliases=options.aliases,
                date_formats=date_formats,
                timestamp_formats=timestamp_formats,
                decimal_separator=options.decimal_separator,
                thousands_separator=options.thousands_separator,
                true_tokens=options.true_tokens,
                false_tokens=options.false_tokens,
            )
        if value_kind == "timestamp" and not timestamp_formats:
            raise ValueError(f"FORMAT_TIMESTAMP_WITH_TIMEZONE_REQUIRED:{target_field}")
        pattern = column.get("pattern")
        if pattern is not None and not isinstance(pattern, str):
            raise ValueError(f"FORMAT_PATTERN_INVALID:{target_field}")
        enum = column.get("enum", ())
        if not isinstance(enum, Sequence) or isinstance(enum, str):
            raise ValueError(f"FORMAT_ENUM_INVALID:{target_field}")
        minimum = _optional_decimal(column.get("min"), target_field)
        maximum = _optional_decimal(column.get("max"), target_field)
        if (minimum is not None or maximum is not None) and value_kind not in {
            "integer",
            "decimal",
        }:
            raise ValueError(f"FORMAT_RANGE_UNSUPPORTED:{target_field}")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError(f"FORMAT_RANGE_INVALID:{target_field}")
        rules.append(
            FormatFieldRule(
                field=source_field,
                target_field=target_field,
                value_kind=value_kind,
                rule_id="CLN-FMT-01",
                required_in_source=target_field not in optional_source_fields,
                options=options,
                pattern=pattern,
                enum=tuple(enum),
                minimum=minimum,
                maximum=maximum,
                max_length=max_length,
                precision=precision,
                scale=scale,
            )
        )
    if not rules:
        raise ValueError("FORMAT_SOURCE_MAPPING_REQUIRED")
    profile = FormatProfile(
        domain=domain,
        entity=entity,
        version=version,
        required_source_columns=tuple(rule.field for rule in rules if rule.required_in_source),
        rules=tuple(rules),
    )
    _validate_profile(profile)
    return profile


def clean_bronze_formats(
    request: ReadRequest,
    profile: FormatProfile,
    *,
    source_system_id: str,
    data_source_id: str,
    dataset_id: str,
    batch_id: str,
    expected_sha256: str,
    expected_rows: int,
) -> FormatResult:
    """Read one pinned Bronze CSV/XLSX object and apply its format profile."""
    _validate_context(
        request,
        profile,
        source_system_id=source_system_id,
        data_source_id=data_source_id,
        dataset_id=dataset_id,
        batch_id=batch_id,
        expected_sha256=expected_sha256,
        expected_rows=expected_rows,
    )
    parsed = read_file_snapshot(
        request,
        SourceReaderFactory.get_reader(request.source_type),
        source_system_id=source_system_id,
        data_source_id=data_source_id,
        dataset_id=dataset_id,
    )
    provenance = parsed.provenance
    if provenance is None:
        raise ValueError("BRONZE_PROVENANCE_REQUIRED")
    if provenance.object_version_id != request.object_version_id:
        raise ValueError("BRONZE_OBJECT_VERSION_MISMATCH")
    if provenance.content_sha256.lower() != expected_sha256.lower():
        raise ValueError("BRONZE_OBJECT_CHECKSUM_MISMATCH")
    if parsed.rows_read != expected_rows:
        raise ValueError("BRONZE_OBJECT_ROW_COUNT_MISMATCH")
    return apply_format_rules(
        columns=parsed.columns,
        rows=parsed.rows,
        locators=parsed.record_locators,
        profile=profile,
        batch_id=batch_id,
        provenance=provenance,
    )


def apply_format_rules(
    *,
    columns: list[str],
    rows: list[tuple[object, ...]],
    locators: tuple[str, ...],
    profile: FormatProfile,
    batch_id: str,
    provenance: FileProvenance,
) -> FormatResult:
    """Normalize only explicitly configured formats and quarantine invalid rows."""
    _validate_profile(profile)
    if not isinstance(batch_id, str) or not batch_id.strip():
        raise ValueError("INVALID_BATCH_ID")
    if not columns or any(not isinstance(column, str) or not column.strip() for column in columns):
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

    accepted: list[FormatRecord] = []
    quarantined: list[FormatRecord] = []
    issues: list[FormatIssue] = []
    normalized_counts: Counter[str] = Counter()
    invalid_counts: Counter[str] = Counter()
    for row, locator in zip(rows, locators, strict=True):
        original = dict(zip(columns, row, strict=True))
        cleaned = dict(original)
        row_issues: list[FormatIssue] = []
        row_normalization_issues: list[FormatIssue] = []
        normalized_fields: set[str] = set()
        invalid = False
        for rule in profile.rules:
            if rule.field not in original:
                continue
            value = original[rule.field]
            if value is None or value == "":
                continue
            try:
                normalized, change_codes = _normalize_and_validate(value, rule)
            except _InvalidFieldValueError as exc:
                invalid = True
                invalid_counts[rule.field] += 1
                row_issues.append(
                    FormatIssue(
                        rule_id=exc.rule_id,
                        rule_version=profile.version,
                        domain=profile.domain,
                        entity=profile.entity,
                        field=rule.field,
                        code=exc.code,
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
        issues.extend(row_issues)
        if invalid:
            quarantined.append(FormatRecord(locator, original))
        else:
            issues.extend(row_normalization_issues)
            normalized_counts.update(normalized_fields)
            accepted.append(FormatRecord(locator, cleaned))
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


def _normalize_and_validate(value: object, rule: FormatFieldRule) -> tuple[object, tuple[str, ...]]:
    changes: list[str] = []
    normalized = value
    if isinstance(normalized, str):
        if rule.options.unicode_nfc:
            nfc = unicodedata.normalize("NFC", normalized)
            if nfc != normalized:
                normalized = nfc
                changes.append("UNICODE_NFC_APPLIED")
        if rule.options.trim:
            trimmed = normalized.strip()
            if trimmed != normalized:
                normalized = trimmed
                changes.append("TRIM_APPLIED")
    if normalized is None or normalized == "":
        return normalized, tuple(changes)

    if rule.value_kind == "string":
        if not isinstance(normalized, str):
            raise _InvalidFieldValueError("INVALID_STRING_TYPE")
        alias_map = dict(rule.options.aliases)
        if normalized in alias_map:
            normalized = alias_map[normalized]
            changes.append("APPROVED_ALIAS_MAPPED")
        if rule.enum and normalized not in rule.enum:
            raise _InvalidFieldValueError("ENUM_VALUE_INVALID")
        if rule.pattern and re.search(rule.pattern, normalized) is None:
            raise _InvalidFieldValueError("PATTERN_VALUE_INVALID")
        if rule.max_length is not None and len(normalized) > rule.max_length:
            raise _InvalidFieldValueError("STRING_LENGTH_INVALID")
        if rule.min_length is not None and len(normalized) < rule.min_length:
            raise _InvalidFieldValueError("STRING_LENGTH_INVALID")
        return normalized, tuple(changes)

    if rule.value_kind == "integer":
        parsed = _parse_integer(normalized)
        _validate_numeric_range(parsed, rule)
        if isinstance(normalized, float | Decimal):
            normalized = int(parsed)
            changes.append("INTEGER_FORMAT_CANONICALIZED")
        return normalized, tuple(changes)

    if rule.value_kind == "decimal":
        parsed_decimal, canonical = _parse_decimal(normalized, rule)
        _validate_numeric_range(parsed_decimal, rule)
        if canonical != normalized:
            changes.append("DECIMAL_FORMAT_CANONICALIZED")
        return canonical, tuple(changes)

    if rule.value_kind == "date":
        _, canonical = _parse_date(normalized, rule.options.date_formats)
        if canonical != normalized:
            changes.append("DATE_FORMAT_CANONICALIZED")
        return canonical, tuple(changes)

    if rule.value_kind == "timestamp":
        _, canonical = _parse_timestamp(normalized, rule.options.timestamp_formats)
        if canonical != normalized:
            changes.append("TIMESTAMP_FORMAT_CANONICALIZED")
        return canonical, tuple(changes)

    if rule.value_kind == "boolean":
        if isinstance(normalized, bool):
            return normalized, tuple(changes)
        if not isinstance(normalized, str):
            raise _InvalidFieldValueError("BOOLEAN_VALUE_INVALID")
        if normalized in rule.options.true_tokens:
            canonical = "true"
        elif normalized in rule.options.false_tokens:
            canonical = "false"
        else:
            raise _InvalidFieldValueError("BOOLEAN_VALUE_INVALID")
        if canonical != normalized:
            changes.append("BOOLEAN_FORMAT_CANONICALIZED")
        return canonical, tuple(changes)
    assert_never(rule.value_kind)


def _parse_integer(value: object) -> Decimal:
    if isinstance(value, bool):
        raise _InvalidFieldValueError("INTEGER_VALUE_INVALID")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, Decimal) and value.is_finite() and value == value.to_integral_value():
        return value
    if isinstance(value, float) and value.is_integer():
        return Decimal(str(value))
    if isinstance(value, str) and re.fullmatch(r"[+-]?[0-9]+", value):
        return Decimal(value)
    raise _InvalidFieldValueError("INTEGER_VALUE_INVALID")


def _parse_decimal(value: object, rule: FormatFieldRule) -> tuple[Decimal, str]:
    if isinstance(value, bool) or not isinstance(value, str | int | float | Decimal):
        raise _InvalidFieldValueError("DECIMAL_VALUE_INVALID")
    if isinstance(value, int | float | Decimal):
        try:
            parsed = Decimal(str(value))
        except InvalidOperation as exc:
            raise _InvalidFieldValueError("DECIMAL_VALUE_INVALID") from exc
        if not parsed.is_finite():
            raise _InvalidFieldValueError("DECIMAL_VALUE_INVALID")
        return _canonical_decimal(parsed, rule)

    source = str(value)
    decimal_separator = rule.options.decimal_separator
    thousands_separator = rule.options.thousands_separator
    sign = ""
    unsigned = source
    if unsigned.startswith(("+", "-")):
        sign, unsigned = unsigned[0], unsigned[1:]
    if unsigned.count(decimal_separator) > 1:
        raise _InvalidFieldValueError("DECIMAL_FORMAT_INVALID")
    integer_part, separator, fraction = unsigned.partition(decimal_separator)
    if not integer_part or (separator and not fraction):
        raise _InvalidFieldValueError("DECIMAL_FORMAT_INVALID")
    if thousands_separator and thousands_separator in integer_part:
        grouping = re.escape(thousands_separator)
        if re.fullmatch(rf"[0-9]{{1,3}}(?:{grouping}[0-9]{{3}})+", integer_part) is None:
            raise _InvalidFieldValueError("DECIMAL_FORMAT_INVALID")
        integer_digits = integer_part.replace(thousands_separator, "")
    else:
        if re.fullmatch(r"[0-9]+", integer_part) is None:
            raise _InvalidFieldValueError("DECIMAL_FORMAT_INVALID")
        integer_digits = integer_part
    if separator and re.fullmatch(r"[0-9]+", fraction) is None:
        raise _InvalidFieldValueError("DECIMAL_FORMAT_INVALID")
    normalized = sign + integer_digits + ("." + fraction if separator else "")
    try:
        parsed = Decimal(normalized)
    except InvalidOperation as exc:
        raise _InvalidFieldValueError("DECIMAL_VALUE_INVALID") from exc
    if not parsed.is_finite():
        raise _InvalidFieldValueError("DECIMAL_VALUE_INVALID")
    return _canonical_decimal(parsed, rule)


def _canonical_decimal(parsed: Decimal, rule: FormatFieldRule) -> tuple[Decimal, str]:
    if rule.scale is not None:
        quantum = Decimal(1).scaleb(-rule.scale)
        try:
            quantized = parsed.quantize(quantum)
        except InvalidOperation as exc:
            raise _InvalidFieldValueError("DECIMAL_PRECISION_INVALID") from exc
        if quantized != parsed:
            raise _InvalidFieldValueError("DECIMAL_SCALE_INVALID")
        canonical = format(quantized, f".{rule.scale}f")
    else:
        canonical = format(parsed, "f")
    if rule.precision is not None and rule.scale is not None:
        integer_capacity = rule.precision - rule.scale
        if abs(parsed) >= Decimal(10) ** integer_capacity:
            raise _InvalidFieldValueError("DECIMAL_PRECISION_INVALID")
    return parsed, canonical


def _parse_date(value: object, formats: tuple[str, ...]) -> tuple[date, str]:
    if isinstance(value, datetime):
        if value.tzinfo is not None or value.time() != time.min:
            raise _InvalidFieldValueError("DATE_TIME_COMPONENT_INVALID", rule_id="CLN-VAL-01")
        parsed = value.date()
    elif not isinstance(value, date | str):
        raise _InvalidFieldValueError("DATE_VALUE_INVALID", rule_id="CLN-FMT-01")
    elif isinstance(value, date):
        parsed = value
    else:
        parsed = None
        for date_format in formats:
            try:
                candidate = datetime.strptime(value, date_format).date()
            except ValueError:
                continue
            if candidate.strftime(date_format) == value:
                parsed = candidate
                break
        if parsed is None:
            raise _InvalidFieldValueError("DATE_FORMAT_INVALID", rule_id="CLN-FMT-01")
    assert parsed is not None
    return parsed, parsed.isoformat()


def _parse_timestamp(value: object, formats: tuple[str, ...]) -> tuple[datetime, str]:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = None
        normalized_value = re.sub(r"([+-][0-9]{2}):([0-9]{2})$", r"\1\2", value)
        if normalized_value.endswith("Z"):
            normalized_value = normalized_value[:-1] + "+0000"
        for timestamp_format in formats:
            try:
                candidate = datetime.strptime(value, timestamp_format)
            except ValueError:
                continue
            if candidate.strftime(timestamp_format) == normalized_value:
                parsed = candidate
                break
        if parsed is None:
            raise _InvalidFieldValueError("TIMESTAMP_FORMAT_INVALID", rule_id="CLN-FMT-01")
    else:
        raise _InvalidFieldValueError("TIMESTAMP_VALUE_INVALID", rule_id="CLN-FMT-01")
    assert parsed is not None
    if parsed.utcoffset() is None:
        raise _InvalidFieldValueError("TIMESTAMP_TIMEZONE_REQUIRED", rule_id="CLN-FMT-01")
    canonical = parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return parsed, canonical


def _validate_numeric_range(value: Decimal, rule: FormatFieldRule) -> None:
    if rule.minimum is not None and value < rule.minimum:
        raise _InvalidFieldValueError("VALUE_BELOW_MINIMUM")
    if rule.maximum is not None and value > rule.maximum:
        raise _InvalidFieldValueError("VALUE_ABOVE_MAXIMUM")


def _schema_value_kind(
    column: Mapping[str, object],
) -> tuple[ValueKind, int | None, int | None, int | None]:
    type_name = column.get("type")
    if not isinstance(type_name, str):
        raise ValueError("FORMAT_SCHEMA_COLUMN_TYPE_REQUIRED")
    lowered = type_name.strip().lower()
    if lowered in {"int", "integer", "bigint", "smallint"}:
        return "integer", None, None, None
    if lowered.startswith("decimal") or lowered.startswith("numeric"):
        dimensions = re.search(r"\((\d+)\s*,\s*(\d+)\)", lowered)
        if dimensions is None:
            return "decimal", None, None, None
        return "decimal", int(dimensions.group(1)), int(dimensions.group(2)), None
    if lowered == "date":
        return "date", None, None, None
    if lowered.startswith("timestamp"):
        return "timestamp", None, None, None
    if lowered == "boolean":
        return "boolean", None, None, None
    if lowered in {"string", "text"} or lowered.startswith(("varchar(", "char(")):
        length = re.search(r"\((\d+)\)", lowered)
        return "string", None, None, int(length.group(1)) if length else None
    raise ValueError(f"FORMAT_SCHEMA_TYPE_UNSUPPORTED:{type_name}")


def _schema_date_format(value: object) -> str:
    if value == "YYYY-MM-DD":
        return "%Y-%m-%d"
    raise ValueError("FORMAT_SOURCE_DATE_FORMAT_REQUIRED")


def _optional_decimal(value: object, field: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        raise ValueError(f"FORMAT_SCHEMA_RANGE_INVALID:{field}")
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError(f"FORMAT_SCHEMA_RANGE_INVALID:{field}")
    return result


def _validate_profile(profile: FormatProfile) -> None:
    if any(
        not isinstance(value, str) or not value.strip()
        for value in (profile.domain, profile.entity, profile.version)
    ):
        raise ValueError("INVALID_FORMAT_PROFILE")
    if not isinstance(profile.rules, tuple) or not profile.rules:
        raise ValueError("FORMAT_RULES_REQUIRED")
    if not isinstance(profile.required_source_columns, tuple) or any(
        not isinstance(field, str) or not field.strip() for field in profile.required_source_columns
    ):
        raise ValueError("INVALID_REQUIRED_SOURCE_COLUMNS")
    fields: set[str] = set()
    for rule in profile.rules:
        if not isinstance(rule, FormatFieldRule):
            raise ValueError("INVALID_FORMAT_RULE")
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (rule.field, rule.target_field, rule.rule_id)
        ):
            raise ValueError("INVALID_FORMAT_RULE")
        if rule.field in fields:
            raise ValueError("DUPLICATE_FORMAT_FIELD_RULE")
        fields.add(rule.field)
        if type(rule.required_in_source) is not bool:
            raise ValueError("INVALID_FORMAT_RULE")
        if rule.value_kind not in (
            "string",
            "integer",
            "decimal",
            "date",
            "timestamp",
            "boolean",
        ):
            raise ValueError("INVALID_FORMAT_VALUE_KIND")
        options = rule.options
        if not isinstance(options, SourceFormatOptions):
            raise ValueError("INVALID_SOURCE_FORMAT_OPTIONS")
        if type(options.trim) is not bool or type(options.unicode_nfc) is not bool:
            raise ValueError("INVALID_FORMAT_OPTION_FLAG")
        if not isinstance(options.decimal_separator, str) or options.decimal_separator not in {
            ".",
            ",",
        }:
            raise ValueError("INVALID_DECIMAL_SEPARATOR")
        if not isinstance(options.thousands_separator, str | type(None)) or (
            options.thousands_separator not in {None, ".", ",", " "}
        ):
            raise ValueError("INVALID_THOUSANDS_SEPARATOR")
        if options.thousands_separator == options.decimal_separator:
            raise ValueError("INVALID_THOUSANDS_SEPARATOR")
        if not isinstance(options.aliases, tuple) or any(
            not isinstance(alias, tuple) or len(alias) != 2 for alias in options.aliases
        ):
            raise ValueError("INVALID_FORMAT_ALIAS")
        if any(
            not isinstance(source, str) or not isinstance(target, str) or not source or not target
            for source, target in options.aliases
        ):
            raise ValueError("INVALID_FORMAT_ALIAS")
        if len(dict(options.aliases)) != len(options.aliases):
            raise ValueError("DUPLICATE_FORMAT_ALIAS")
        if not isinstance(options.date_formats, tuple) or not isinstance(
            options.timestamp_formats, tuple
        ):
            raise ValueError("INVALID_SOURCE_FORMAT")
        if rule.value_kind == "date" and not options.date_formats:
            raise ValueError("FORMAT_DATE_FORMAT_REQUIRED")
        if rule.value_kind == "timestamp" and not options.timestamp_formats:
            raise ValueError("FORMAT_TIMESTAMP_FORMAT_REQUIRED")
        if rule.value_kind == "timestamp" and any(
            "%z" not in timestamp_format for timestamp_format in options.timestamp_formats
        ):
            raise ValueError("FORMAT_TIMESTAMP_TIMEZONE_FORMAT_REQUIRED")
        if any(
            not isinstance(source_format, str) or not source_format
            for source_format in (*options.date_formats, *options.timestamp_formats)
        ):
            raise ValueError("INVALID_SOURCE_FORMAT")
        if not isinstance(options.true_tokens, tuple) or not isinstance(
            options.false_tokens, tuple
        ):
            raise ValueError("INVALID_BOOLEAN_TOKENS")
        if any(
            not isinstance(token, str) or not token
            for token in (*options.true_tokens, *options.false_tokens)
        ):
            raise ValueError("INVALID_BOOLEAN_TOKENS")
        if len(set(options.true_tokens)) != len(options.true_tokens) or len(
            set(options.false_tokens)
        ) != len(options.false_tokens):
            raise ValueError("DUPLICATE_BOOLEAN_TOKENS")
        if rule.pattern is not None and not isinstance(rule.pattern, str):
            raise ValueError("INVALID_FORMAT_PATTERN")
        if rule.pattern:
            try:
                re.compile(rule.pattern)
            except re.error as exc:
                raise ValueError("INVALID_FORMAT_PATTERN") from exc
        if not isinstance(rule.enum, tuple) or any(
            not isinstance(value, str) for value in rule.enum
        ):
            raise ValueError("INVALID_FORMAT_ENUM")
        if not set(options.true_tokens).isdisjoint(options.false_tokens):
            raise ValueError("BOOLEAN_TOKEN_CONFLICT")
        if rule.value_kind not in {"integer", "decimal"} and (
            rule.minimum is not None or rule.maximum is not None
        ):
            raise ValueError("FORMAT_RANGE_UNSUPPORTED")
        for bound in (rule.minimum, rule.maximum):
            if bound is not None and (not isinstance(bound, Decimal) or not bound.is_finite()):
                raise ValueError("INVALID_FORMAT_RANGE")
        if rule.minimum is not None and rule.maximum is not None and rule.minimum > rule.maximum:
            raise ValueError("INVALID_FORMAT_RANGE")
        if rule.precision is not None and (
            isinstance(rule.precision, bool)
            or not isinstance(rule.precision, int)
            or rule.precision < 1
        ):
            raise ValueError("INVALID_FORMAT_PRECISION")
        for length in (rule.min_length, rule.max_length):
            if length is not None and (
                isinstance(length, bool) or not isinstance(length, int) or length < 0
            ):
                raise ValueError("INVALID_FORMAT_LENGTH")
        if (
            rule.min_length is not None
            and rule.max_length is not None
            and rule.min_length > rule.max_length
        ):
            raise ValueError("INVALID_FORMAT_LENGTH")
        if rule.scale is not None and (
            isinstance(rule.scale, bool)
            or not isinstance(rule.scale, int)
            or rule.scale < 0
            or (rule.precision is not None and rule.scale > rule.precision)
        ):
            raise ValueError("INVALID_FORMAT_SCALE")
    expected_required = {rule.field for rule in profile.rules if rule.required_in_source}
    if expected_required != set(profile.required_source_columns):
        raise ValueError("FORMAT_REQUIRED_SOURCE_COLUMNS_MISMATCH")
    if len(fields) != len(profile.rules) or len(set(profile.required_source_columns)) != len(
        profile.required_source_columns
    ):
        raise ValueError("DUPLICATE_REQUIRED_SOURCE_COLUMNS")


def _validate_context(
    request: ReadRequest,
    profile: FormatProfile,
    *,
    source_system_id: str,
    data_source_id: str,
    dataset_id: str,
    batch_id: str,
    expected_sha256: str,
    expected_rows: int,
) -> None:
    _validate_profile(profile)
    if any(
        not isinstance(value, str) or not value.strip()
        for value in (source_system_id, data_source_id, dataset_id, batch_id)
    ):
        raise ValueError("INVALID_BRONZE_CONTEXT")
    if not isinstance(request.source_type, str) or not request.source_type.strip():
        raise ValueError("UNSUPPORTED_BRONZE_FILE_TYPE")
    source_type = request.source_type.strip().upper()
    if source_type not in {"CSV", "EXCEL"}:
        raise ValueError("UNSUPPORTED_BRONZE_FILE_TYPE")
    if not isinstance(request.location, str):
        raise ValueError("BRONZE_OBJECT_URI_REQUIRED")
    parsed = urlsplit(request.location)
    if parsed.scheme.lower() != "s3" or not parsed.netloc or not parsed.path.strip("/"):
        raise ValueError("BRONZE_OBJECT_URI_REQUIRED")
    object_name = parsed.path.rsplit("/", 1)[-1].lower()
    if source_type == "CSV" and not object_name.endswith(".csv"):
        raise ValueError("BRONZE_FILE_TYPE_MISMATCH")
    if source_type == "EXCEL" and not object_name.endswith(".xlsx"):
        raise ValueError("BRONZE_FILE_TYPE_MISMATCH")
    if not request.source_table or request.source_table != parsed.path.rsplit("/", 1)[-1]:
        raise ValueError("BRONZE_SOURCE_TABLE_MISMATCH")
    if (
        not isinstance(request.object_version_id, str)
        or not request.object_version_id.strip()
        or request.object_version_id == "null"
    ):
        raise ValueError("BRONZE_OBJECT_VERSION_REQUIRED")
    if (
        not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
        or any(character not in "0123456789abcdefABCDEF" for character in expected_sha256)
    ):
        raise ValueError("BRONZE_OBJECT_CHECKSUM_REQUIRED")
    if isinstance(expected_rows, bool) or not isinstance(expected_rows, int) or expected_rows < 0:
        raise ValueError("BRONZE_OBJECT_ROW_COUNT_REQUIRED")
