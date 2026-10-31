"""Read pinned Bronze CSV/XLSX objects and apply configured duplicate-key rules."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit

from app.modules.ingestion.readers.factory import SourceReaderFactory
from app.modules.ingestion.readers.file_snapshot import read_file_snapshot

if TYPE_CHECKING:
    from app.modules.ingestion.contracts import FileProvenance
    from app.modules.ingestion.readers.contract import ReadRequest

DuplicateKeyKind = Literal["PRIMARY_KEY", "UNIQUE", "COMPOSITE_UNIQUE", "BUSINESS_KEY"]
DuplicateDisposition = Literal["COLLAPSED", "QUARANTINED"]


@dataclass(frozen=True)
class DuplicateKeyRule:
    """A uniqueness criterion expressed in parsed source-column names."""

    rule_id: str
    fields: tuple[str, ...]
    kind: DuplicateKeyKind


@dataclass(frozen=True)
class DuplicateProfile:
    """Versioned duplicate policy bound to a source namespace and entity."""

    domain: str
    entity: str
    version: str
    required_source_columns: tuple[str, ...]
    key_rules: tuple[DuplicateKeyRule, ...]
    collapse_exact_primary_key_duplicates: bool = False


@dataclass(frozen=True)
class DuplicateRecord:
    locator: str
    values: Mapping[str, object]
    source_locators: tuple[str, ...]


@dataclass(frozen=True)
class DuplicateIssue:
    rule_id: str
    rule_version: str
    domain: str
    entity: str
    code: str
    disposition: DuplicateDisposition
    locator: str
    related_locator: str
    key_fields: tuple[str, ...]


@dataclass(frozen=True)
class DuplicateResult:
    domain: str
    entity: str
    batch_id: str
    records_read: int
    passed: tuple[DuplicateRecord, ...]
    quarantined: tuple[DuplicateRecord, ...]
    issues: tuple[DuplicateIssue, ...]
    duplicate_groups_by_rule: Mapping[str, int]
    duplicate_records_by_rule: Mapping[str, int]
    provenance: FileProvenance


def build_duplicate_profile(
    schema: Mapping[str, object],
    *,
    domain: str,
    entity: str,
    version: str,
    target_to_source: Mapping[str, str],
    additional_key_rules: tuple[DuplicateKeyRule, ...] = (),
    collapse_exact_primary_key_duplicates: bool = False,
) -> DuplicateProfile:
    """Build key rules from a domain schema and explicit raw-to-target field mapping."""
    entities = schema.get("entities")
    if not isinstance(entities, Mapping):
        raise ValueError("DUPLICATE_SCHEMA_ENTITIES_REQUIRED")
    entity_schema = entities.get(entity)
    if not isinstance(entity_schema, Mapping) or "primary_key" not in entity_schema:
        raise ValueError("DUPLICATE_ENTITY_SCHEMA_REQUIRED")

    primary_fields = _field_tuple(entity_schema.get("primary_key"), "PRIMARY_KEY")
    rules: list[DuplicateKeyRule] = [
        DuplicateKeyRule(
            rule_id=f"{domain.upper()}-{entity.upper()}-PK",
            fields=_map_fields(primary_fields, target_to_source),
            kind="PRIMARY_KEY",
        )
    ]
    for column in entity_schema.get("columns", ()):
        if isinstance(column, Mapping) and column.get("unique") is True:
            target_field = column.get("name")
            if not isinstance(target_field, str) or not target_field.strip():
                raise ValueError("DUPLICATE_SCHEMA_UNIQUE_FIELD_INVALID")
            source_fields = _map_fields((target_field,), target_to_source)
            rules.append(
                DuplicateKeyRule(
                    rule_id=f"{domain.upper()}-{entity.upper()}-UNIQUE-{target_field}",
                    fields=source_fields,
                    kind="UNIQUE",
                )
            )
    unique_together = entity_schema.get("unique_together", ())
    if not isinstance(unique_together, Sequence) or isinstance(unique_together, str):
        raise ValueError("DUPLICATE_SCHEMA_UNIQUE_KEYS_INVALID")
    for index, target_fields_value in enumerate(unique_together, start=1):
        target_fields = _field_tuple(target_fields_value, "COMPOSITE_UNIQUE")
        rules.append(
            DuplicateKeyRule(
                rule_id=f"{domain.upper()}-{entity.upper()}-COMPOSITE-UNIQUE-{index}",
                fields=_map_fields(target_fields, target_to_source),
                kind="COMPOSITE_UNIQUE",
            )
        )

    rules.extend(additional_key_rules)
    deduplicated_rules: list[DuplicateKeyRule] = []
    seen_field_sets: set[tuple[str, ...]] = set()
    for rule in rules:
        if rule.fields not in seen_field_sets:
            deduplicated_rules.append(rule)
            seen_field_sets.add(rule.fields)
    required_columns = tuple(
        dict.fromkeys(field for rule in deduplicated_rules for field in rule.fields)
    )
    profile = DuplicateProfile(
        domain=domain,
        entity=entity,
        version=version,
        required_source_columns=required_columns,
        key_rules=tuple(deduplicated_rules),
        collapse_exact_primary_key_duplicates=collapse_exact_primary_key_duplicates,
    )
    _validate_profile(profile)
    return profile


def clean_bronze_duplicates(
    request: ReadRequest,
    profile: DuplicateProfile,
    *,
    source_system_id: str,
    data_source_id: str,
    dataset_id: str,
    batch_id: str,
    expected_sha256: str,
    expected_rows: int,
) -> DuplicateResult:
    """Read one pinned Bronze CSV/XLSX object and handle duplicate-key collisions."""
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

    return detect_and_handle_duplicates(
        columns=parsed.columns,
        rows=parsed.rows,
        locators=parsed.record_locators,
        profile=profile,
        batch_id=batch_id,
        provenance=provenance,
    )


def detect_and_handle_duplicates(
    *,
    columns: list[str],
    rows: list[tuple[object, ...]],
    locators: tuple[str, ...],
    profile: DuplicateProfile,
    batch_id: str,
    provenance: FileProvenance,
) -> DuplicateResult:
    """Detect configured key collisions; never fuzzy-match or discard conflicting rows."""
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

    records = tuple(dict(zip(columns, row, strict=True)) for row in rows)
    field_positions = {column: index for index, column in enumerate(columns)}
    duplicate_groups: dict[int, list[tuple[DuplicateKeyRule, tuple[int, ...]]]] = defaultdict(list)
    groups_by_rule: dict[str, int] = {}
    rows_by_rule: dict[str, int] = {}

    for rule_index, rule in enumerate(profile.key_rules):
        grouped: dict[tuple[object, ...], list[int]] = defaultdict(list)
        for row_index, row in enumerate(rows):
            key = tuple(row[field_positions[field]] for field in rule.fields)
            if any(_is_incomplete_key_value(value) for value in key):
                continue
            grouped[key].append(row_index)
        collisions = [tuple(indices) for indices in grouped.values() if len(indices) > 1]
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
        identical_payload = all(records[index] == records[indices[0]] for index in indices[1:])
        same_primary_key = _same_key_group(indices, rows, field_positions, primary_rule.fields)
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


def load_schema(path: Path) -> Mapping[str, object]:
    """Load a checked-in JSON domain schema for building a source-bound profile."""
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, Mapping):
        raise ValueError("DUPLICATE_SCHEMA_INVALID")
    return loaded


def _field_tuple(value: object, kind: str) -> tuple[str, ...]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, str)
        or not value
        or any(not isinstance(field, str) or not field.strip() for field in value)
    ):
        raise ValueError(f"DUPLICATE_SCHEMA_{kind}_INVALID")
    return tuple(value)


def _map_fields(
    target_fields: tuple[str, ...], target_to_source: Mapping[str, str]
) -> tuple[str, ...]:
    source_fields: list[str] = []
    for target_field in target_fields:
        source_field = target_to_source.get(target_field)
        if not isinstance(source_field, str) or not source_field.strip():
            raise ValueError(f"DUPLICATE_KEY_SOURCE_MAPPING_REQUIRED:{target_field}")
        source_fields.append(source_field)
    return tuple(source_fields)


def _validate_profile(profile: DuplicateProfile) -> None:
    if any(
        not isinstance(value, str) or not value.strip()
        for value in (profile.domain, profile.entity, profile.version)
    ):
        raise ValueError("INVALID_DUPLICATE_PROFILE")
    if not isinstance(profile.collapse_exact_primary_key_duplicates, bool):
        raise ValueError("INVALID_DUPLICATE_PROFILE")
    if not isinstance(profile.key_rules, tuple) or not profile.key_rules:
        raise ValueError("DUPLICATE_KEY_RULES_REQUIRED")
    if any(not isinstance(rule, DuplicateKeyRule) for rule in profile.key_rules):
        raise ValueError("INVALID_DUPLICATE_KEY_RULE")
    primary_rules = [rule for rule in profile.key_rules if rule.kind == "PRIMARY_KEY"]
    if len(primary_rules) != 1:
        raise ValueError("DUPLICATE_PRIMARY_KEY_RULE_REQUIRED")
    for rule in profile.key_rules:
        if (
            not isinstance(rule.rule_id, str)
            or not rule.rule_id.strip()
            or not isinstance(rule.fields, tuple)
            or not rule.fields
            or any(not isinstance(field, str) or not field.strip() for field in rule.fields)
            or len(rule.fields) != len(set(rule.fields))
            or rule.kind not in ("PRIMARY_KEY", "UNIQUE", "COMPOSITE_UNIQUE", "BUSINESS_KEY")
        ):
            raise ValueError("INVALID_DUPLICATE_KEY_RULE")
    rule_ids = tuple(rule.rule_id for rule in profile.key_rules)
    if len(set(rule_ids)) != len(rule_ids):
        raise ValueError("DUPLICATE_RULE_ID")
    if not isinstance(profile.required_source_columns, tuple) or any(
        not isinstance(column, str) or not column.strip()
        for column in profile.required_source_columns
    ):
        raise ValueError("INVALID_REQUIRED_SOURCE_COLUMNS")
    all_key_fields = {field for rule in profile.key_rules for field in rule.fields}
    if not all_key_fields.issubset(profile.required_source_columns):
        raise ValueError("DUPLICATE_KEY_COLUMNS_MUST_BE_REQUIRED")


def _validate_context(
    request: ReadRequest,
    profile: DuplicateProfile,
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


def _is_incomplete_key_value(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _same_key_group(
    indices: tuple[int, ...],
    rows: list[tuple[object, ...]],
    field_positions: Mapping[str, int],
    fields: tuple[str, ...],
) -> bool:
    first_key = tuple(rows[indices[0]][field_positions[field]] for field in fields)
    return all(
        tuple(rows[index][field_positions[field]] for field in fields) == first_key
        for index in indices[1:]
    )
