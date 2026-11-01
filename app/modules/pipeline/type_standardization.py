"""Bộ chuyển đổi S3 có kết quả xác định và bàn giao từ S2; không I/O hay phân giải định danh."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from types import MappingProxyType
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.modules.pipeline.schema_mapping import MappingKey

if TYPE_CHECKING:
    from app.modules.ingestion.contracts import FileProvenance
    from app.modules.pipeline.schema_mapping import MappingIssue, MappingResult


class ConversionError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ConversionConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class FieldOptions:
    null_tokens: tuple[str, ...] = ()
    trim: bool = False
    decimal_separator: str = "."
    thousands_separator: str | None = None
    date_format: str = "%Y-%m-%d"
    timestamp_format: str = "iso"
    source_timezone: str | None = None
    true_tokens: tuple[str, ...] = ("true",)
    false_tokens: tuple[str, ...] = ("false",)
    excel_epoch: str | None = None  # 1900 hoặc 1904; chỉ dùng khi được cấu hình tường minh.
    invalid_optional_to_null: bool = False


def _format_pattern(fmt: str, *, timestamp: bool) -> str:
    # Bắt buộc format mô tả đủ ngày/giờ trước khi gọi strptime; nếu thiếu thành phần,
    # strptime có thể tự điền năm 1900 hoặc giờ 00:00:00 mà không báo lỗi.
    """Yêu cầu thành phần lịch/thời gian tường minh; %% đã thoát chỉ là ký tự nguyên văn.

    Chỉ thị dạng số giúp tránh phân tích phụ thuộc vùng miền và các giả định ngầm về thế kỷ/năm,
    AM/PM, ngày theo tuần hay tên múi giờ trong strptime.
    """
    code = "INVALID_TIMESTAMP_FORMAT" if timestamp else "INVALID_DATE_FORMAT"
    if not isinstance(fmt, str) or not fmt:
        raise ConversionConfigurationError(code)
    patterns = {"Y": r"[0-9]{4}", "m": r"[0-9]{2}", "d": r"[0-9]{2}"}
    if timestamp:
        patterns.update(
            H=r"[0-9]{2}",
            M=r"[0-9]{2}",
            S=r"[0-9]{2}",
            f=r"[0-9]{1,6}",
            z=r"(?:Z|[+-][0-9]{2}:?[0-9]{2})",
        )
    seen: set[str] = set()
    parts: list[str] = []
    index = 0
    while index < len(fmt):
        char = fmt[index]
        index += 1
        if char != "%":
            parts.append(re.escape(char))
            continue
        if index == len(fmt):
            raise ConversionConfigurationError(code)
        directive = fmt[index]
        index += 1
        if directive == "%":
            parts.append("%")
        elif directive not in patterns or directive in seen:
            raise ConversionConfigurationError(code)
        else:
            seen.add(directive)
            parts.append(patterns[directive])
    required = {"Y", "m", "d", "H", "M", "S"} if timestamp else {"Y", "m", "d"}
    if not required <= seen:
        raise ConversionConfigurationError(code)
    return "".join(parts)


def _check_options(options: FieldOptions) -> None:
    if not isinstance(options, FieldOptions):
        raise ConversionConfigurationError("INVALID_FIELD_OPTIONS")
    for tokens in (options.null_tokens, options.true_tokens, options.false_tokens):
        if not isinstance(tokens, tuple) or any(not isinstance(token, str) for token in tokens):
            raise ConversionConfigurationError("INVALID_OPTION_TOKENS")
        if len(tokens) != len(set(tokens)):
            raise ConversionConfigurationError("DUPLICATE_OPTION_TOKENS")
    if type(options.trim) is not bool or type(options.invalid_optional_to_null) is not bool:
        raise ConversionConfigurationError("INVALID_OPTION_FLAG")
    if options.source_timezone is not None and (
        not isinstance(options.source_timezone, str) or not options.source_timezone
    ):
        raise ConversionConfigurationError("UNKNOWN_SOURCE_TIMEZONE")
    _format_pattern(options.date_format, timestamp=False)
    if options.timestamp_format != "iso":
        _format_pattern(options.timestamp_format, timestamp=True)
    if not isinstance(options.decimal_separator, str) or options.decimal_separator not in {
        ".",
        ",",
    }:
        raise ConversionConfigurationError("INVALID_DECIMAL_SEPARATOR")
    if (
        not isinstance(options.thousands_separator, str | type(None))
        or options.thousands_separator not in {None, ".", ",", " "}
        or options.thousands_separator == options.decimal_separator
    ):
        raise ConversionConfigurationError("INVALID_THOUSANDS_SEPARATOR")
    if set(options.true_tokens) & set(options.false_tokens):
        raise ConversionConfigurationError("BOOLEAN_TOKEN_CONFLICT")
    if set(options.null_tokens) & (set(options.true_tokens) | set(options.false_tokens)):
        raise ConversionConfigurationError("NULL_TOKEN_CONFLICT")
    if not isinstance(options.excel_epoch, str | type(None)) or options.excel_epoch not in {
        None,
        "1900",
        "1904",
    }:
        raise ConversionConfigurationError("INVALID_EXCEL_EPOCH")
    if options.source_timezone:
        try:
            ZoneInfo(options.source_timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ConversionConfigurationError("UNKNOWN_SOURCE_TIMEZONE") from exc


def _nullable(spec: Mapping[str, Any]) -> bool:
    return isinstance(spec.get("type"), list | tuple) and "null" in spec["type"]


def _freeze(value: Any) -> Any:
    # Khóa cả các cấu trúc lồng nhau, tránh người gọi sửa contract sau khi khởi tạo.
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _validate_contract(contract: Mapping[str, Any], key: MappingKey) -> dict[str, Any]:
    # Lỗi contract phải được phát hiện trước khi xử lý dữ liệu, kể cả batch rỗng.
    """Kiểm tra biến thể S1 có thể thực thi, gồm các ràng buộc JSON Schema bỏ qua."""
    try:
        # Bản chụp JSON độc lập cũng từ chối đối tượng không thể biểu diễn và số không hữu hạn.
        snapshot: dict[str, Any] = json.loads(json.dumps(dict(contract), allow_nan=False))
        if snapshot.get("x-version") != key.target_schema_version:
            raise ConversionConfigurationError("CONVERSION_VERSION_MISMATCH")
        spec = snapshot.get("$defs", {}).get(key.entity)
        if not isinstance(spec, dict):
            raise ConversionConfigurationError("ENTITY_NOT_FOUND")
        if spec.get("x-owner") != key.domain_id:
            raise ConversionConfigurationError("DOMAIN_MISMATCH")
        props = spec.get("properties")
        if (
            spec.get("type") != "object"
            or spec.get("additionalProperties") is not False
            or not isinstance(props, dict)
            or not props
        ):
            raise ConversionConfigurationError("INVALID_ENTITY_CONTRACT")
        required = spec.get("required")
        if (
            not isinstance(required, list)
            or len(required) != len(set(required))
            or set(required) != set(props)
        ):
            raise ConversionConfigurationError("INVALID_REQUIRED_FIELDS")
        keys = spec.get("x-primary-key")
        if (
            not isinstance(keys, list)
            or not keys
            or len(keys) != len(set(keys))
            or not set(keys) <= set(props)
        ):
            raise ConversionConfigurationError("INVALID_PRIMARY_KEY")
        for group in spec.get("x-unique-together", []):
            if (
                not isinstance(group, list)
                or not group
                or len(group) != len(set(group))
                or not set(group) <= set(props)
            ):
                raise ConversionConfigurationError("INVALID_UNIQUE_CONSTRAINT")
        for name, prop in props.items():
            if not isinstance(name, str) or not name or not isinstance(prop, dict):
                raise ConversionConfigurationError("INVALID_FIELD_CONTRACT")
            physical = prop.get("x-physical-type")
            if not isinstance(physical, str):
                raise ConversionConfigurationError("PHYSICAL_TYPE_REQUIRED")
            decimal_match = re.fullmatch(r"decimal\(([0-9]+),([0-9]+)\)", physical)
            varchar_match = re.fullmatch(r"varchar\(([0-9]+)\)", physical)
            logical = "string"
            if physical in {"int", "bigint", "int32", "int64"}:
                logical = "integer"
            elif physical in {"boolean", "bool"}:
                logical = "boolean"
            elif decimal_match:
                precision, scale = map(int, decimal_match.groups())
                if (
                    not 1 <= precision <= 38
                    or not 0 <= scale <= precision
                    or type(prop.get("x-precision")) is not int
                    or type(prop.get("x-scale")) is not int
                    or (prop["x-precision"], prop["x-scale"]) != (precision, scale)
                ):
                    raise ConversionConfigurationError("INVALID_DECIMAL_CONTRACT")
            elif varchar_match:
                length = int(varchar_match[1])
                if length < 1 or prop.get("maxLength") != length:
                    raise ConversionConfigurationError("INVALID_STRING_CONTRACT")
            elif physical not in {"utf8", "text", "date32", "timestamp[us,UTC]"}:
                raise ConversionConfigurationError("UNSUPPORTED_PHYSICAL_TYPE")
            types = prop.get("type")
            if types != logical and types not in ([logical, "null"], ["null", logical]):
                raise ConversionConfigurationError("PHYSICAL_LOGICAL_TYPE_MISMATCH")
            if name in keys and (_nullable(prop) or logical != "integer"):
                raise ConversionConfigurationError("INVALID_PRIMARY_KEY")
            if "pattern" in prop:
                if not isinstance(prop["pattern"], str):
                    raise ConversionConfigurationError("INVALID_PATTERN")
                try:
                    re.compile(prop["pattern"])
                except re.error as exc:
                    raise ConversionConfigurationError("INVALID_PATTERN") from exc
            for bound in ("minimum", "maximum", "x-minimum", "x-maximum"):
                if bound in prop and (
                    type(prop[bound]) not in (int, float)
                    or (logical not in {"integer"} and not decimal_match)
                ):
                    raise ConversionConfigurationError("INVALID_RANGE")
            low, high = (
                prop.get("minimum", prop.get("x-minimum")),
                prop.get("maximum", prop.get("x-maximum")),
            )
            if low is not None and high is not None and low > high:
                raise ConversionConfigurationError("INVALID_RANGE")
            for length_key in ("minLength", "maxLength"):
                if length_key in prop and (
                    type(prop[length_key]) is not int or prop[length_key] < 0
                ):
                    raise ConversionConfigurationError("INVALID_STRING_CONTRACT")
            if prop.get("minLength", 0) > prop.get("maxLength", float("inf")):
                raise ConversionConfigurationError("INVALID_STRING_CONTRACT")
            if "enum" in prop:
                enum = prop["enum"]
                if (
                    logical != "string"
                    or not isinstance(enum, list)
                    or not enum
                    or any(
                        not isinstance(v, str) and not (v is None and _nullable(prop)) for v in enum
                    )
                    or len(enum) != len(set(enum))
                ):
                    raise ConversionConfigurationError("INVALID_ENUM")
            if "x-references" in prop:
                ref = prop["x-references"]
                if (
                    logical != "integer"
                    or not isinstance(ref, dict)
                    or any(
                        not isinstance(ref.get(k), str) or not ref[k] for k in ("entity", "column")
                    )
                ):
                    raise ConversionConfigurationError("INVALID_REFERENCE")
            if "x-derived-from" in prop and (
                name != "birth_year"
                or prop["x-derived-from"] != "date_of_birth"
                or logical != "integer"
            ):
                raise ConversionConfigurationError("UNSUPPORTED_DERIVATION")
        return snapshot
    except ConversionConfigurationError:
        raise
    except (TypeError, ValueError, AttributeError, KeyError) as exc:
        raise ConversionConfigurationError("INVALID_CONTRACT") from exc


def _decimal(value: object, spec: Mapping[str, Any], options: FieldOptions) -> Decimal:
    # Giữ độ chính xác Decimal theo precision/scale; không âm thầm làm tròn dữ liệu.
    if isinstance(value, bool | float):
        raise ConversionError("DECIMAL_PARSE_ERROR")
    if isinstance(value, str):
        point = re.escape(options.decimal_separator)
        group = re.escape(options.thousands_separator) if options.thousands_separator else None
        integer = rf"(?:[0-9]+|[0-9]{{1,3}}(?:{group}[0-9]{{3}})+)" if group else r"[0-9]+"
        if not re.fullmatch(rf"[+-]?{integer}(?:{point}[0-9]+)?", value):
            raise ConversionError("DECIMAL_PARSE_ERROR")
        if options.thousands_separator:
            value = value.replace(options.thousands_separator, "")
        value = value.replace(options.decimal_separator, ".")
    if not isinstance(value, str | int | Decimal):
        raise ConversionError("DECIMAL_PARSE_ERROR")
    try:
        number = Decimal(value)
        if not number.is_finite():
            raise ConversionError("DECIMAL_PARSE_ERROR")
        precision, scale = spec["x-precision"], spec["x-scale"]
        if number.as_tuple().exponent < -scale:
            raise ConversionError("DECIMAL_SCALE_ERROR")
        if number.copy_abs() >= Decimal(10) ** (precision - scale):
            raise ConversionError("DECIMAL_OVERFLOW")
        with localcontext() as context:
            context.prec = max(precision + 2, 40)
            return number.quantize(Decimal(1).scaleb(-scale))
    except InvalidOperation as exc:
        raise ConversionError("DECIMAL_PARSE_ERROR") from exc


def _date(value: object, options: FieldOptions) -> date:
    if isinstance(value, datetime):
        raise ConversionError("DATE_TIME_LOSS")
    if isinstance(value, date):
        return value
    if isinstance(value, int | float | Decimal) and not isinstance(value, bool):
        if not options.excel_epoch:
            raise ConversionError("EXCEL_EPOCH_REQUIRED")
        serial = Decimal(str(value))
        if not serial.is_finite() or serial != serial.to_integral_value() or serial < 0:
            raise ConversionError("EXCEL_DATE_ERROR")
        # Excel hệ 1900 có ngày giả 29/02/1900 ở serial 60: phải từ chối.
        # Các serial sau đó cần trừ một ngày để khớp lịch thực tế.
        if options.excel_epoch == "1900" and serial == 60:
            raise ConversionError("EXCEL_FICTITIOUS_DATE")
        epoch = date(1899, 12, 31) if options.excel_epoch == "1900" else date(1904, 1, 1)
        days = int(serial) - (1 if options.excel_epoch == "1900" and serial > 60 else 0)
        try:
            return epoch + timedelta(days=days)
        except (OverflowError, ValueError) as exc:
            raise ConversionError("EXCEL_DATE_ERROR") from exc
    if not isinstance(value, str):
        raise ConversionError("DATE_PARSE_ERROR")
    try:
        if not re.fullmatch(_format_pattern(options.date_format, timestamp=False), value):
            raise ValueError("incomplete source date")
        result = datetime.strptime(value, options.date_format).date()
        return result
    except ValueError as exc:
        raise ConversionError("DATE_PARSE_ERROR") from exc


def _timestamp(value: object, options: FieldOptions) -> datetime:
    if isinstance(value, str):
        # Python âm thầm cắt bớt chữ số thập phân dư; S3 phải từ chối trường hợp này.
        if re.search(r"[.,][0-9]{7,}", value):
            raise ConversionError("TIMESTAMP_PRECISION_ERROR")
        try:
            if options.timestamp_format == "iso":
                if not re.fullmatch(
                    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})?",
                    value,
                ):
                    raise ValueError("invalid ISO timestamp")
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            else:
                if not re.fullmatch(
                    _format_pattern(options.timestamp_format, timestamp=True), value
                ):
                    raise ValueError("incomplete source timestamp")
                value = datetime.strptime(value, options.timestamp_format)
        except ValueError as exc:
            raise ConversionError("TIMESTAMP_PARSE_ERROR") from exc
    if not isinstance(value, datetime):
        raise ConversionError("TIMESTAMP_PARSE_ERROR")
    if value.tzinfo is None:
        if not options.source_timezone:
            raise ConversionError("TIMEZONE_REQUIRED")
        zone = ZoneInfo(options.source_timezone)
        # Khi đổi giờ mùa hè, giờ địa phương có thể không tồn tại hoặc xảy ra hai lần.
        # Thử cả hai fold rồi kiểm tra vòng về UTC để phát hiện, thay vì tự đoán giờ.
        candidates = [value.replace(tzinfo=zone, fold=i) for i in (0, 1)]
        valid = [
            dt
            for dt in candidates
            if dt.astimezone(UTC).astimezone(zone).replace(tzinfo=None) == value
        ]
        if not valid:
            raise ConversionError("NONEXISTENT_LOCAL_TIME")
        if len({dt.utcoffset() for dt in valid}) > 1:
            raise ConversionError("AMBIGUOUS_LOCAL_TIME")
        value = valid[0]
    try:
        return value.astimezone(UTC)
    except (OverflowError, ValueError) as exc:
        raise ConversionError("TIMESTAMP_PARSE_ERROR") from exc


def convert_value(
    value: object, spec: Mapping[str, Any], options: FieldOptions = FieldOptions()
) -> object:
    """Trả về giá trị đúng kiểu gốc; không âm thầm chuyển lỗi tùy chọn thành null ở đây."""
    _check_options(options)
    if isinstance(value, str) and options.trim:
        value = value.strip()
    if value is None or (isinstance(value, str) and value in options.null_tokens):
        if not _nullable(spec):
            raise ConversionError("REQUIRED_VALUE_MISSING")
        return None
    physical = spec["x-physical-type"]
    if physical in {"bigint", "int", "int32", "int64"}:
        if isinstance(value, bool) or not isinstance(value, str | int):
            raise ConversionError("INTEGER_PARSE_ERROR")
        if isinstance(value, str) and not re.fullmatch(r"[+-]?[0-9]+", value):
            raise ConversionError("INTEGER_PARSE_ERROR")
        try:
            value = int(value)
        except ValueError as exc:
            raise ConversionError("INTEGER_OVERFLOW") from exc
        bits = 64 if physical in {"bigint", "int64"} else 32
        if not -(2 ** (bits - 1)) <= value < 2 ** (bits - 1):
            raise ConversionError("INTEGER_OVERFLOW")
    elif physical.startswith("decimal"):
        value = _decimal(value, spec, options)
    elif physical == "date32":
        value = _date(value, options)
    elif physical == "timestamp[us,UTC]":
        try:
            value = _timestamp(value, options)
        except ConversionError:
            raise
        except (OverflowError, ValueError) as exc:
            raise ConversionError("TIMESTAMP_PARSE_ERROR") from exc
    elif physical in {"boolean", "bool"}:
        if not isinstance(value, bool):
            if isinstance(value, str) and value in options.true_tokens:
                value = True
            elif isinstance(value, str) and value in options.false_tokens:
                value = False
            else:
                raise ConversionError("INVALID_BOOLEAN")
    elif physical.startswith("varchar") or physical in {"utf8", "text"}:
        if not isinstance(value, str):
            raise ConversionError("STRING_TYPE_ERROR")
        value = unicodedata.normalize("NFC", value)
        if len(value) > spec.get("maxLength", len(value)) or len(value) < spec.get("minLength", 0):
            raise ConversionError("STRING_LENGTH_ERROR")
        if "pattern" in spec and re.search(spec["pattern"], value) is None:
            raise ConversionError("PATTERN_ERROR")
    else:
        raise ConversionConfigurationError("UNSUPPORTED_PHYSICAL_TYPE")
    if isinstance(value, int | Decimal) and not isinstance(value, bool):
        low, high = (
            spec.get("minimum", spec.get("x-minimum")),
            spec.get("maximum", spec.get("x-maximum")),
        )
        if (low is not None and value < Decimal(str(low))) or (
            high is not None and value > Decimal(str(high))
        ):
            raise ConversionError("VALUE_RANGE_ERROR")
    return value


@dataclass(frozen=True)
class TypeIssue:
    locator: str
    field: str
    code: str
    severity: str
    rule_version: str


@dataclass(frozen=True)
class TypedRow:
    locator: str
    values: dict[str, object]
    pending_s4: dict[str, object]


@dataclass(frozen=True)
class TypeResult:
    key: MappingKey
    batch_id: str
    input_version: str
    conversion_version: str
    accepted: tuple[TypedRow, ...]
    rejected_locators: tuple[str, ...]
    issues: tuple[TypeIssue, ...]
    mapping_issues: tuple[MappingIssue, ...]
    preview: bool
    inspected_count: int
    total_count: int
    configuration_fingerprint: str
    silver_ready: bool = False
    provenance: FileProvenance | None = None
    processing_steps: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, init=False)
class TypeStandardizationService:
    key: MappingKey
    version: str
    spec: Mapping[str, Any]
    options: Mapping[str, FieldOptions]
    configuration_fingerprint: str
    value_mapping_fields: tuple[str, ...]

    def __init__(
        self,
        *,
        key: MappingKey,
        conversion_version: str,
        contract: Mapping[str, Any],
        options: Mapping[str, FieldOptions] | None = None,
        value_mapping_fields: tuple[str, ...] = (),
    ) -> None:
        if not isinstance(key, MappingKey) or any(
            not isinstance(value, str) or not value.strip() for value in vars(key).values()
        ):
            raise ConversionConfigurationError("INVALID_MAPPING_KEY")
        if not isinstance(conversion_version, str) or not conversion_version.strip():
            raise ConversionConfigurationError("CONVERSION_VERSION_MISMATCH")
        snapshot = _validate_contract(contract, key)
        spec = snapshot["$defs"][key.entity]
        if (
            type(value_mapping_fields) is not tuple
            or any(not isinstance(n, str) for n in value_mapping_fields)
            or len(set(value_mapping_fields)) != len(value_mapping_fields)
        ):
            raise ConversionConfigurationError("INVALID_VALUE_MAPPING_FIELDS")
        for name in value_mapping_fields:
            field = spec["properties"].get(name, {})
            if (
                name.startswith("_")
                or name in spec["x-primary-key"]
                or "x-references" in field
                or "x-derived-from" in field
                or "string" not in field.get("type", ())
            ):
                raise ConversionConfigurationError("INVALID_VALUE_MAPPING_FIELDS")
            physical = field.get("x-physical-type", "")
            if physical not in {"utf8", "text"} and not physical.startswith("varchar("):
                raise ConversionConfigurationError("INVALID_VALUE_MAPPING_FIELDS")
        configured = dict(options or {})
        if set(configured) - set(spec["properties"]):
            raise ConversionConfigurationError("UNKNOWN_FIELD_OPTIONS")
        for name, option in configured.items():
            _check_options(option)
            if option.invalid_optional_to_null and not _nullable(spec["properties"][name]):
                raise ConversionConfigurationError("REQUIRED_FIELD_CANNOT_NULL_ON_ERROR")
            if option.invalid_optional_to_null and (
                name in spec["x-primary-key"] or "x-references" in spec["properties"][name]
            ):
                raise ConversionConfigurationError("IDENTITY_CANNOT_NULL_ON_ERROR")
        effective = {name: configured.get(name, FieldOptions()) for name in spec["properties"]}
        payload = {
            "engine": "s3-contract-v1",
            "key": asdict(key),
            "version": conversion_version,
            "contract": snapshot,
            "options": {name: asdict(option) for name, option in effective.items()},
            "value_mapping_fields": sorted(value_mapping_fields),
        }
        fingerprint = hashlib.sha256(
            json.dumps(
                payload, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "version", conversion_version)
        # Fingerprint và cấu hình bất biến phải đi cùng nhau để kết quả có thể tái lập.
        object.__setattr__(self, "spec", _freeze(spec))
        object.__setattr__(self, "options", MappingProxyType(effective))
        object.__setattr__(self, "configuration_fingerprint", fingerprint)
        object.__setattr__(self, "value_mapping_fields", tuple(sorted(value_mapping_fields)))

    def standardize(
        self,
        mapped: MappingResult,
        *,
        derivation_inputs: Mapping[str, Mapping[str, object]] | None = None,
        derivation_input_version: str | None = None,
    ) -> TypeResult:
        if mapped.key != self.key:
            raise ConversionConfigurationError("MAPPING_KEY_MISMATCH")
        if mapped.provenance is not None and (
            mapped.provenance.input_version != mapped.input_version
            or mapped.provenance.source_system_id != mapped.key.source_system_id
        ):
            raise ConversionConfigurationError("PROVENANCE_MISMATCH")
        if derivation_inputs is not None and derivation_input_version != mapped.input_version:
            raise ConversionConfigurationError("DERIVATION_VERSION_MISMATCH")
        accepted: list[TypedRow] = []
        rejected: list[str] = list(mapped.rejected_locators)
        issues: list[TypeIssue] = []
        properties = self.spec["properties"]
        for row in mapped.accepted:
            values: dict[str, object] = {}
            pending: dict[str, object] = {}
            failed = False
            if set(row.values) - set(properties):
                raise ConversionConfigurationError("UNDECLARED_TARGET_FIELD")
            for name, spec in properties.items():
                if name.startswith("_"):
                    continue  # Bổ sung thông tin lần chạy/kiểm toán thuộc P5.
                option = self.options.get(name, FieldOptions())
                raw: object
                try:
                    derived = spec.get("x-derived-from")
                    if derived:
                        inputs = (derivation_inputs or {}).get(row.locator, {})
                        if derived not in inputs:
                            raise ConversionError("DERIVATION_INPUT_REQUIRED")
                        birth = convert_value(
                            inputs[derived],
                            {"type": ["string", "null"], "x-physical-type": "date32"},
                            option,
                        )
                        raw = birth.year if isinstance(birth, date) else None
                    else:
                        if name not in row.values:
                            raise ConversionError("REQUIRED_SOURCE_FIELD_MISSING")
                        raw = row.values[name]
                    if (
                        name in self.spec["x-primary-key"]
                        or "x-references" in spec
                        or "enum" in spec
                        or name in self.value_mapping_fields
                    ):
                        # Giữ định danh nguồn và nhãn chưa nhận diện đến S4.
                        if raw is None and not _nullable(spec):
                            raise ConversionError("REQUIRED_VALUE_MISSING")
                        if "enum" in spec or name in self.value_mapping_fields:
                            # Bí danh nguồn có thể dài hơn hoặc khác mã đích.
                            # S4 kiểm tra giá trị đã ánh xạ theo toàn bộ đặc tả đích.
                            pending[name] = convert_value(
                                raw, {"type": spec["type"], "x-physical-type": "utf8"}, option
                            )
                        else:
                            if isinstance(raw, str) and raw in option.null_tokens:
                                raw = None
                            if raw is None and not _nullable(spec):
                                raise ConversionError("REQUIRED_VALUE_MISSING")
                            if raw is not None and (
                                isinstance(raw, bool) or not isinstance(raw, str | int)
                            ):
                                raise ConversionError("SOURCE_ID_TYPE_ERROR")
                            # Giữ nguyên mã nguồn cho S4 đối chiếu; ép "007" thành 7
                            # ở bước này có thể làm mất thông tin nhận diện.
                            pending[name] = deepcopy(raw)
                        continue
                    values[name] = convert_value(raw, spec, option)
                except ConversionError as exc:
                    allow_null = (
                        option.invalid_optional_to_null
                        and _nullable(spec)
                        and exc.code
                        not in {"REQUIRED_SOURCE_FIELD_MISSING", "DERIVATION_INPUT_REQUIRED"}
                    )
                    issues.append(
                        TypeIssue(
                            row.locator,
                            name,
                            exc.code,
                            "warning" if allow_null else "error",
                            self.version,
                        )
                    )
                    if allow_null:
                        if "enum" in spec or name in self.value_mapping_fields:
                            pending[name] = None
                        else:
                            values[name] = None
                    else:
                        failed = True
            if failed:
                rejected.append(row.locator)
            else:
                accepted.append(TypedRow(row.locator, values, pending))
        return TypeResult(
            mapped.key,
            mapped.batch_id,
            mapped.input_version,
            self.version,
            tuple(accepted),
            tuple(rejected),
            tuple(issues),
            mapped.issues,
            mapped.preview,
            mapped.inspected_count,
            mapped.total_count,
            self.configuration_fingerprint,
            provenance=mapped.provenance,
            processing_steps=mapped.processing_steps,
        )
