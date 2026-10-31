"""Ánh xạ cấu trúc cố định phiên bản. Không ép kiểu, phân giải định danh hay ghi lưu trữ."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.modules.ingestion.contracts import BronzeBatch, FileProvenance


@dataclass(frozen=True)
class MappingKey:
    source_system_id: str
    domain_id: str
    entity: str
    source_schema_version: str
    mapping_id: str
    mapping_version: str
    target_schema_version: str


@dataclass(frozen=True)
class FieldMapping:
    target: str
    # JSON Pointer: /a/b, /a~1b, /items/0/id. Không ngầm hỗ trợ ký tự đại diện hoặc tách mảng thành dòng.
    sources: tuple[str, ...]
    operation: str = "copy"  # Các phép ánh xạ: copy, coalesce, concat, split
    separator: str = " "
    index: int = 0
    conflict: str = "error"  # Chỉ với coalesce: error hoặc first_present


@dataclass(frozen=True)
class MappingProfile:
    key: MappingKey
    source_type: str
    target_fields: tuple[str, ...]
    fields: tuple[FieldMapping, ...]
    # Các trường này thuộc giai đoạn sau; không tự sinh null hay giá trị mặc định cho chúng.
    deferred_fields: tuple[str, ...] = ()
    ignored_sources: tuple[str, ...] = ()
    # Đường dẫn nguồn bắt buộc xét theo cấu trúc; giá trị null hiện hữu không phải cột bị thiếu.
    source_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class MappingIssue:
    code: str
    severity: str
    locator: str | None
    field: str | None
    # Thông báo cố ý loại bỏ giá trị thô và thông tin xác thực.


class MappingConfigurationError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class MappedRow:
    locator: str
    values: dict[str, object]
    # Bản xem trước chỉ hiển thị đầu vào đã ánh xạ; các trường chủ động loại bỏ không xuất hiện ở đây.
    before: dict[str, object] | None = None


@dataclass(frozen=True)
class MappingResult:
    key: MappingKey
    batch_id: str
    input_version: str
    accepted: tuple[MappedRow, ...]
    rejected_locators: tuple[str, ...]
    issues: tuple[MappingIssue, ...]
    inspected_count: int
    total_count: int
    preview: bool
    # Đây là cấu trúc trung gian, chưa phải Silver đã kiểm tra.
    silver_ready: bool = False
    provenance: FileProvenance | None = None


def _tokens(pointer: str) -> tuple[str, ...]:
    if not pointer.startswith("/"):
        raise MappingConfigurationError("INVALID_SOURCE_PATH")
    parts = pointer[1:].split("/")
    for part in parts:
        i = 0
        while i < len(part):
            if part[i] == "~":
                if i + 1 >= len(part) or part[i + 1] not in "01":
                    raise MappingConfigurationError("INVALID_SOURCE_PATH")
                i += 1
            i += 1
    return tuple(part.replace("~1", "/").replace("~0", "~") for part in parts)


def _lookup(record: object, pointer: str) -> tuple[bool, object]:
    # Tách trạng thái tồn tại khỏi giá trị: thiếu trường khác với trường có giá trị null.
    # S2 kiểm tra cấu trúc; việc null có hợp lệ hay không được xử lý ở bước sau.
    node = record
    for part in _tokens(pointer):
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif (
            isinstance(node, list)
            and part.isdecimal()
            and str(int(part)) == part
            and int(part) < len(node)
        ):
            node = node[int(part)]
        else:
            return False, None
    return True, node


def _leaves(node: object, prefix: str = "") -> set[str]:
    if isinstance(node, dict) and node:
        result: set[str] = set()
        for key, value in node.items():
            escaped = str(key).replace("~", "~0").replace("/", "~1")
            result.update(_leaves(value, prefix + "/" + escaped))
        return result
    if isinstance(node, list) and node:
        result = set()
        for index, value in enumerate(node):
            result.update(_leaves(value, prefix + "/" + str(index)))
        return result
    return {prefix}


def validate_profile(profile: MappingProfile) -> None:
    if not all(isinstance(value, str) and value.strip() for value in vars(profile.key).values()):
        raise MappingConfigurationError("INCOMPLETE_MAPPING_KEY")
    if not profile.source_type.strip():
        raise MappingConfigurationError("SOURCE_TYPE_REQUIRED")
    targets = [field.target for field in profile.fields]
    if len(targets) != len(set(targets)):
        raise MappingConfigurationError("DUPLICATE_TARGET")
    if len(profile.target_fields) != len(set(profile.target_fields)) or not profile.target_fields:
        raise MappingConfigurationError("INVALID_TARGET_FIELDS")
    if len(profile.deferred_fields) != len(set(profile.deferred_fields)):
        raise MappingConfigurationError("DUPLICATE_DEFERRED_FIELD")
    if set(targets) & set(profile.deferred_fields):
        raise MappingConfigurationError("TARGET_PRODUCER_CONFLICT")
    # Mỗi trường đích phải được ánh xạ hoặc giao rõ cho bước sau sinh ra.
    # Không tự lấp trường chưa có quy tắc bằng null vì có thể che lỗi cấu hình.
    if set(targets) | set(profile.deferred_fields) != set(profile.target_fields):
        raise MappingConfigurationError("TARGET_COVERAGE_ERROR")
    used = {p for field in profile.fields for p in field.sources}
    if any(
        left == right or left.startswith(right + "/") or right.startswith(left + "/")
        for left in used
        for right in profile.ignored_sources
    ):
        raise MappingConfigurationError("SOURCE_POLICY_CONFLICT")
    for path in (*profile.source_fields, *profile.ignored_sources, *used):
        _tokens(path)
    for field in profile.fields:
        if not field.sources or len(field.sources) != len(set(field.sources)):
            raise MappingConfigurationError("INVALID_FIELD_SOURCES")
        if field.operation not in {"copy", "coalesce", "concat", "split"}:
            raise MappingConfigurationError("UNSUPPORTED_OPERATION")
        if field.operation in {"copy", "split"} and len(field.sources) != 1:
            raise MappingConfigurationError("INVALID_OPERATION_ARITY")
        if field.conflict not in {"error", "first_present"}:
            raise MappingConfigurationError("INVALID_CONFLICT_POLICY")
        if field.operation == "split" and (
            not field.separator or isinstance(field.index, bool) or field.index < 0
        ):
            raise MappingConfigurationError("INVALID_SPLIT_CONFIG")
        if profile.source_fields and not set(field.sources) <= set(profile.source_fields):
            raise MappingConfigurationError("UNDECLARED_SOURCE_PATH")


class MappingRegistry:
    def __init__(self, profiles: Sequence[MappingProfile] = ()) -> None:
        self._profiles: dict[MappingKey, MappingProfile] = {}
        for profile in profiles:
            self.register(profile)

    def register(self, profile: MappingProfile) -> None:
        # Một khóa phiên bản chỉ có một cấu hình, để chạy lại không đổi ý nghĩa mapping.
        validate_profile(profile)
        if profile.key in self._profiles:
            raise MappingConfigurationError("MAPPING_VERSION_ALREADY_REGISTERED")
        self._profiles[profile.key] = profile

    def get(self, key: MappingKey) -> MappingProfile:
        try:
            return self._profiles[key]
        except KeyError as exc:
            raise MappingConfigurationError("MAPPING_NOT_FOUND") from exc


def _value(record: Mapping[str, object], field: FieldMapping) -> tuple[object, str | None]:
    # Chỉ biến đổi cấu trúc ở S2; không ép kiểu hoặc suy đoán định danh tại đây.
    # Sao chép giá trị để kết quả mapping không làm thay đổi bản ghi nguồn.
    values = [_lookup(record, pointer) for pointer in field.sources]
    if field.operation == "coalesce":
        present = [value for exists, value in values if exists]
        if not present:
            return None, "REQUIRED_SOURCE_FIELD_MISSING"
        # null là giá trị hiện hữu; không âm thầm chuyển từ null tường minh sang bí danh khác.
        if field.conflict == "error" and any(
            type(v) is not type(present[0]) or v != present[0] for v in present[1:]
        ):
            return None, "MAPPING_CONFLICT"
        return deepcopy(present[0]), None
    if not all(exists for exists, _ in values):
        return None, "REQUIRED_SOURCE_FIELD_MISSING"
    if field.operation == "copy":
        return deepcopy(values[0][1]), None
    if not all(isinstance(value, str) for _, value in values):
        return None, "STRUCTURAL_OPERATION_TYPE_ERROR"
    strings = [str(value) for _, value in values]
    if field.operation == "concat":
        return field.separator.join(strings), None
    parts = strings[0].split(field.separator)
    if field.index >= len(parts):
        return None, "SPLIT_INDEX_MISSING"
    return parts[field.index], None


class SchemaMappingService:
    def __init__(self, registry: MappingRegistry) -> None:
        self.registry = registry

    def map_batch(self, batch: BronzeBatch, *, preview_limit: int | None = None) -> MappingResult:
        """Ánh xạ batch đã cố định. Bên gọi cung cấp vị trí vật lý; không phát sinh ghi lưu trữ."""
        if batch.mapping_binding is None:
            raise MappingConfigurationError("MAPPING_BINDING_REQUIRED")
        binding = batch.mapping_binding
        key = MappingKey(
            binding.source_system_id,
            batch.domain_id,
            binding.entity,
            binding.source_schema_version,
            binding.mapping_id,
            binding.mapping_version,
            binding.target_schema_version,
        )
        profile = self.registry.get(key)
        if not batch.metadata.get("input_version"):
            raise MappingConfigurationError("INPUT_VERSION_REQUIRED")
        if batch.provenance is not None and (
            batch.provenance.input_version != batch.metadata["input_version"]
            or batch.provenance.source_system_id != binding.source_system_id
            or batch.provenance.source_type != batch.source_type
        ):
            raise MappingConfigurationError("PROVENANCE_MISMATCH")
        if profile.source_type != batch.source_type:
            raise MappingConfigurationError("SOURCE_TYPE_MISMATCH")
        if (
            len(batch.record_locators) != len(batch.records)
            or len(set(batch.record_locators)) != len(batch.records)
            or any(not s.strip() for s in batch.record_locators)
        ):
            raise MappingConfigurationError("INVALID_RECORD_LOCATORS")
        if preview_limit is not None and (isinstance(preview_limit, bool) or preview_limit <= 0):
            raise MappingConfigurationError("INVALID_PREVIEW_LIMIT")
        # Kiểm tra cấu trúc đã khai báo trước khi ánh xạ, kể cả khi batch rỗng.
        declared = set(batch.source_paths)
        for path in declared:
            _tokens(path)
        if not declared:
            raise MappingConfigurationError("SOURCE_SHAPE_REQUIRED")
        for field in profile.fields:
            present = [p in declared for p in field.sources]
            if not (any(present) if field.operation == "coalesce" else all(present)):
                raise MappingConfigurationError("REQUIRED_SOURCE_FIELD_MISSING")
        inspected = batch.records if preview_limit is None else batch.records[:preview_limit]
        accepted: list[MappedRow] = []
        rejected: list[str] = []
        issues: list[MappingIssue] = []
        consumed = {p for field in profile.fields for p in field.sources} | set(
            profile.ignored_sources
        )
        for path in sorted(declared - consumed):
            issues.append(MappingIssue("UNMAPPED_SOURCE_FIELD", "warning", None, path))
        for record, locator in zip(inspected, batch.record_locators, strict=False):
            mapped: dict[str, object] = {}
            failed = False
            for field in profile.fields:
                value, error = _value(record, field)
                if error:
                    failed = True
                    issues.append(MappingIssue(error, "error", locator, field.target))
                else:
                    mapped[field.target] = value
            for path in sorted(_leaves(record) - {""}):
                if not any(path == p or path.startswith(p + "/") for p in consumed):
                    issues.append(MappingIssue("UNMAPPED_SOURCE_FIELD", "warning", locator, path))
            if failed:
                rejected.append(locator)
            else:
                before = None
                if preview_limit is not None:
                    before = {}
                    for path in sorted({p for field in profile.fields for p in field.sources}):
                        exists, value = _lookup(record, path)
                        if exists:
                            before[path] = deepcopy(value)
                accepted.append(MappedRow(locator, mapped, before))
        return MappingResult(
            key,
            batch.batch_id,
            batch.metadata["input_version"],
            tuple(accepted),
            tuple(rejected),
            tuple(issues),
            len(inspected),
            len(batch.records),
            preview_limit is not None,
            provenance=batch.provenance,
        )

    def preview(self, batch: BronzeBatch, *, limit: int = 20) -> MappingResult:
        return self.map_batch(batch, preview_limit=limit)
