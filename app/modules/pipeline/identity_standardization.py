"""Phân giải định danh/từ điển từ S3 → S4, không công bố hay suy đoán giá trị khớp."""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime
from types import MappingProxyType
from typing import TYPE_CHECKING

from app.modules.pipeline.identity_contracts import (
    CrosswalkSnapshot,
    IdentityConfigurationError,
    digest,
    source_key,
    utc,
)
from app.modules.pipeline.type_standardization import ConversionError, convert_value

if TYPE_CHECKING:
    from app.modules.ingestion.contracts import FileProvenance
    from app.modules.pipeline.schema_mapping import MappingIssue, MappingKey
    from app.modules.pipeline.type_standardization import (
        TypeIssue,
        TypeResult,
        TypeStandardizationService,
    )


@dataclass(frozen=True)
class ValueDictionary:
    version: str
    aliases: tuple[tuple[str, str], ...] = ()
    trim: bool = False
    case: str = "preserve"
    allow_canonical: bool = True

    def normalize(self, value: str) -> str:
        result = unicodedata.normalize("NFC", value.strip() if self.trim else value)
        return result if self.case == "preserve" else getattr(result, self.case)()


@dataclass(frozen=True)
class IdentityIssue:
    locator: str
    field: str
    code: str
    severity: str
    rule_version: str


@dataclass(frozen=True)
class StandardizedRow:
    locator: str
    values: dict[str, object]


@dataclass(frozen=True)
class IdentityResult:
    key: MappingKey
    batch_id: str
    input_version: str
    conversion_version: str
    conversion_fingerprint: str
    identity_version: str
    crosswalk_version: str
    crosswalk_fingerprint: str
    dictionary_versions: dict[str, str]
    configuration_fingerprint: str
    effective_at: str
    accepted: tuple[StandardizedRow, ...]
    rejected_locators: tuple[str, ...]
    issues: tuple[IdentityIssue, ...]
    type_issues: tuple[TypeIssue, ...]
    mapping_issues: tuple[MappingIssue, ...]
    preview: bool
    inspected_count: int
    total_count: int
    provenance: FileProvenance | None
    silver_ready: bool = False
    processing_steps: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, init=False)
class IdentityStandardizationService:
    types: TypeStandardizationService
    version: str
    crosswalk: CrosswalkSnapshot
    dictionaries: Mapping[str, ValueDictionary]
    references: Mapping[str, str]
    effective_at: datetime
    configuration_fingerprint: str

    def __init__(
        self,
        *,
        types: TypeStandardizationService,
        version: str,
        crosswalk: CrosswalkSnapshot,
        dictionaries: Mapping[str, ValueDictionary],
        reference_namespaces: Mapping[str, str],
        effective_at: datetime,
    ) -> None:
        if not isinstance(version, str) or not version.strip():
            raise IdentityConfigurationError("IDENTITY_VERSION_REQUIRED")
        if not isinstance(crosswalk, CrosswalkSnapshot):
            raise IdentityConfigurationError("CROSSWALK_SNAPSHOT_REQUIRED")
        when = utc(effective_at)
        props = types.spec["properties"]
        pk = types.spec["x-primary-key"]
        if len(pk) > 1 and any("x-references" not in props[n] for n in pk):
            raise IdentityConfigurationError("UNSUPPORTED_COMPOSITE_IDENTITY")
        refs = {n for n, s in props.items() if "x-references" in s}
        if set(reference_namespaces) != refs or any(
            not isinstance(v, str) or not v.strip() for v in reference_namespaces.values()
        ):
            raise IdentityConfigurationError("REFERENCE_NAMESPACE_REQUIRED")
        value_fields = {n for n, s in props.items() if "enum" in s} | set(
            types.value_mapping_fields
        )
        if set(dictionaries) != value_fields:
            raise IdentityConfigurationError("DICTIONARY_BINDING_MISMATCH")
        for name, rule in dictionaries.items():
            if (
                not isinstance(rule, ValueDictionary)
                or not isinstance(rule.version, str)
                or not rule.version.strip()
            ):
                raise IdentityConfigurationError("DICTIONARY_VERSION_REQUIRED")
            if (
                type(rule.aliases) is not tuple
                or rule.case not in {"preserve", "upper", "lower", "casefold"}
                or type(rule.trim) is not bool
                or type(rule.allow_canonical) is not bool
            ):
                raise IdentityConfigurationError("INVALID_DICTIONARY")
            seen = set()
            for pair in rule.aliases:
                if (
                    type(pair) is not tuple
                    or len(pair) != 2
                    or any(not isinstance(v, str) for v in pair)
                ):
                    raise IdentityConfigurationError("INVALID_DICTIONARY")
                alias, target = pair
                normalized = rule.normalize(alias)
                if not normalized or normalized in seen:
                    raise IdentityConfigurationError("AMBIGUOUS_VALUE_MAPPING")
                seen.add(normalized)
                try:
                    convert_value(target, props[name])
                except ConversionError as exc:
                    raise IdentityConfigurationError("INVALID_DICTIONARY_TARGET") from exc
                if "enum" in props[name] and target not in props[name]["enum"]:
                    raise IdentityConfigurationError("INVALID_DICTIONARY_TARGET")
                if rule.allow_canonical and any(
                    rule.normalize(c) == normalized and c != target
                    for c in props[name].get("enum", ())
                    if isinstance(c, str)
                ):
                    raise IdentityConfigurationError("CANONICAL_VALUE_REMAP")
            canonical = [
                rule.normalize(c) for c in props[name].get("enum", ()) if isinstance(c, str)
            ]
            if len(canonical) != len(set(canonical)):
                raise IdentityConfigurationError("AMBIGUOUS_VALUE_MAPPING")
        payload = {
            "engine": "s4-v1",
            "types": types.configuration_fingerprint,
            "version": version,
            "crosswalk": crosswalk.fingerprint,
            "dictionaries": {k: asdict(v) for k, v in dictionaries.items()},
            "references": dict(reference_namespaces),
            "effective_at": when.isoformat(),
        }
        for name, value in {
            "types": types,
            "version": version,
            "crosswalk": crosswalk,
            "dictionaries": MappingProxyType(dict(dictionaries)),
            "references": MappingProxyType(dict(reference_namespaces)),
            "effective_at": when,
            "configuration_fingerprint": digest(payload),
        }.items():
            object.__setattr__(self, name, value)

    def standardize(self, typed: TypeResult) -> IdentityResult:
        if (
            typed.key != self.types.key
            or typed.conversion_version != self.types.version
            or typed.configuration_fingerprint != self.types.configuration_fingerprint
        ):
            raise IdentityConfigurationError("TYPE_HANDOFF_MISMATCH")
        if typed.provenance is not None and (
            typed.provenance.input_version != typed.input_version
            or typed.provenance.source_system_id != typed.key.source_system_id
        ):
            raise IdentityConfigurationError("PROVENANCE_MISMATCH")
        locators = [r.locator for r in typed.accepted] + list(typed.rejected_locators)
        if (
            not typed.input_version
            or len(locators) != len(set(locators))
            or len(locators) != typed.inspected_count
            or typed.total_count < typed.inspected_count
            or (not typed.preview and typed.total_count != typed.inspected_count)
            or any(not isinstance(loc, str) or not loc.strip() for loc in locators)
        ):
            raise IdentityConfigurationError("INVALID_TYPE_HANDOFF")
        props = self.types.spec["properties"]
        pk = self.types.spec["x-primary-key"]
        pending_fields = {
            n for n, s in props.items() if n in pk or "x-references" in s or "enum" in s
        } | set(self.types.value_mapping_fields)
        ordinary = {n for n in props if not n.startswith("_")} - pending_fields
        index = {(e.namespace, e.entity, e.source): e for e in self.crosswalk.entries}
        accepted = []
        rejected = list(typed.rejected_locators)
        issues: list[IdentityIssue] = []

        def issue(locator: str, field: str, code: str) -> None:
            issues.append(IdentityIssue(locator, field, code, "error", self.version))

        def resolve(namespace: str, entity: str, raw: object) -> int | None:
            # Mã chỉ có nghĩa trong namespace + entity và thời gian hiệu lực crosswalk.
            # Không đoán hai mã giống nhau ở hai hệ thống là cùng một đối tượng.
            try:
                key = source_key((raw,))
            except IdentityConfigurationError:
                return None
            entry = index.get((namespace, entity, key))
            if (
                entry is None
                or not utc(entry.valid_from) <= self.effective_at
                or (entry.valid_to is not None and self.effective_at >= utc(entry.valid_to))
            ):
                return None
            return entry.canonical_id

        for row in typed.accepted:
            if set(row.pending_s4) != pending_fields or set(row.values) != ordinary:
                raise IdentityConfigurationError("PENDING_FIELDS_MISMATCH")
            values = deepcopy(row.values)
            start = len(issues)
            for name in sorted(pending_fields):
                raw, spec = row.pending_s4[name], props[name]
                if raw is None:
                    if "null" in spec["type"]:
                        values[name] = None
                    else:
                        issue(
                            row.locator,
                            name,
                            "SOURCE_ID_MISSING"
                            if name in pk or "x-references" in spec
                            else "REQUIRED_VALUE_MISSING",
                        )
                    continue
                if "x-references" in spec:
                    ref = spec["x-references"]
                    target = resolve(self.references[name], ref["entity"], raw)
                    if target is None:
                        issue(row.locator, name, "REFERENCE_PENDING")
                    else:
                        values[name] = target
                elif name in pk:
                    if len(pk) != 1:
                        raise IdentityConfigurationError("UNSUPPORTED_COMPOSITE_IDENTITY")
                    target = resolve(typed.key.source_system_id, typed.key.entity, raw)
                    if target is None:
                        issue(row.locator, name, "SOURCE_ID_UNMAPPED")
                    else:
                        values[name] = target
                else:
                    rule = self.dictionaries[name]
                    if not isinstance(raw, str):
                        issue(row.locator, name, "VALUE_TYPE_ERROR")
                        continue
                    aliases = {rule.normalize(a): b for a, b in rule.aliases}
                    normalized = rule.normalize(raw)
                    target_value = aliases.get(normalized)
                    if target_value is None and rule.allow_canonical:
                        canonical = {
                            rule.normalize(c): c for c in spec.get("enum", ()) if isinstance(c, str)
                        }
                        target_value = canonical.get(normalized) if "enum" in spec else normalized
                    if target_value is None or (
                        "enum" in spec and target_value not in spec["enum"]
                    ):
                        issue(row.locator, name, "UNKNOWN_REFERENCE_CODE")
                        continue
                    try:
                        values[name] = convert_value(target_value, spec)
                    except ConversionError as exc:
                        issue(row.locator, name, exc.code)
            if typed.key.entity == "lecturer" and len(issues) == start:
                own_id = resolve(
                    typed.key.source_system_id, "lecturer", row.pending_s4["employee_id"]
                )
                if own_id != values["employee_id"]:
                    issue(row.locator, "employee_id", "SHARED_IDENTITY_MISMATCH")
            if len(issues) == start:
                accepted.append(StandardizedRow(row.locator, values))
            else:
                rejected.append(row.locator)
        # Từ chối mọi dòng có xung đột sau chuẩn hóa, không chỉ dòng đến sau cùng.
        groups: dict[tuple[object, ...], list[StandardizedRow]] = {}
        for standardized in accepted:
            groups.setdefault(tuple(standardized.values[n] for n in pk), []).append(standardized)
        # Nếu nhiều dòng quy về cùng khóa chuẩn, loại toàn bộ nhóm xung đột.
        # Giữ dòng đầu/cuối sẽ khiến kết quả phụ thuộc thứ tự đọc và có thể mất dữ liệu.
        collisions = {r.locator for group in groups.values() if len(group) > 1 for r in group}
        for standardized in accepted:
            if standardized.locator in collisions:
                rejected.append(standardized.locator)
                issue(standardized.locator, ",".join(pk), "CANONICAL_KEY_COLLISION")
        return IdentityResult(
            typed.key,
            typed.batch_id,
            typed.input_version,
            typed.conversion_version,
            typed.configuration_fingerprint,
            self.version,
            self.crosswalk.version,
            self.crosswalk.fingerprint,
            {k: v.version for k, v in self.dictionaries.items()},
            self.configuration_fingerprint,
            self.effective_at.isoformat(),
            tuple(r for r in accepted if r.locator not in collisions),
            tuple(rejected),
            tuple(issues),
            typed.issues,
            typed.mapping_issues,
            typed.preview,
            typed.inspected_count,
            typed.total_count,
            typed.provenance,
            processing_steps=typed.processing_steps,
        )
