"""Check keys across the original batch before publishing a subset.

This is an admission check, not another cleaning policy: it never repairs a
row, changes Bronze, or chooses a winner among rejected and accepted records.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from app.modules.pipeline.cleaning.format_validation import apply_format_rules
from app.modules.pipeline.cleaning.missing_values import apply_missing_value_rules

if TYPE_CHECKING:
    from app.modules.pipeline.cleaning.pipeline import CleaningTask


def partial_key_evidence(task: CleaningTask, rejected: set[str]) -> dict[str, Any]:
    batch = task.batch
    provenance = batch.provenance
    if provenance is None:
        raise ValueError("BRONZE_PROVENANCE_REQUIRED")
    blockers: list[dict[str, Any]] = []
    profile = task.duplicate_profile

    def block(
        code: str, locator: str, rule_id: str, fields: tuple[str, ...], related: str | None = None
    ) -> None:
        blockers.append(
            {
                "code": code,
                "locator": locator,
                "related_locator": related,
                "rule_id": rule_id,
                "rule_version": profile.version,
                "key_fields": list(fields),
            }
        )

    if rejected and not profile.key_rules:
        block("PARTIAL_KEY_EVIDENCE_INCOMPLETE", "batch", "PARTIAL-KEY-01", ())
    for key_rule in profile.key_rules:
        fields = key_rule.fields
        missing_rules = tuple(rule for rule in task.missing_profile.rules if rule.field in fields)
        format_rules = tuple(rule for rule in task.format_profile.rules if rule.field in fields)
        if len(missing_rules) != len(fields) or len(format_rules) != len(fields):
            block("PARTIAL_KEY_EVIDENCE_INCOMPLETE", "batch", key_rule.rule_id, fields)
            continue
        missing = apply_missing_value_rules(
            columns=list(fields),
            rows=[tuple(row[field] for field in fields) for row in batch.records],
            locators=batch.record_locators,
            profile=replace(
                task.missing_profile, required_source_columns=fields, rules=missing_rules
            ),
            batch_id=batch.batch_id,
            provenance=provenance,
        )
        formatted = apply_format_rules(
            columns=list(fields),
            rows=[tuple(row.values[field] for field in fields) for row in missing.passed],
            locators=tuple(row.locator for row in missing.passed),
            profile=replace(
                task.format_profile, required_source_columns=fields, rules=format_rules
            ),
            batch_id=batch.batch_id,
            provenance=provenance,
        )
        invalid_locators = [row.locator for row in missing.quarantined] + [
            row.locator for row in formatted.quarantined
        ]
        for locator in invalid_locators:
            block("PARTIAL_KEY_EVIDENCE_INCOMPLETE", locator, key_rule.rule_id, fields)
        groups: dict[tuple[object, ...], list[str]] = defaultdict(list)
        for row in formatted.passed:
            key = tuple(row.values[field] for field in fields)
            if any(value is None or value == "" for value in key):
                # Nullable UNIQUE keys do not assert identity. Missing PK does.
                if key_rule.kind == "PRIMARY_KEY":
                    block("PARTIAL_KEY_EVIDENCE_INCOMPLETE", row.locator, key_rule.rule_id, fields)
                continue
            try:
                groups[key].append(row.locator)
            except TypeError:
                block("PARTIAL_KEY_EVIDENCE_INCOMPLETE", row.locator, key_rule.rule_id, fields)
        for locators in groups.values():
            rejected_group = sorted(set(locators) & rejected)
            remaining = sorted(set(locators) - rejected)
            if rejected_group and remaining:
                for locator in remaining:
                    block(
                        "PARTIAL_KEY_CONFLICT_WITH_QUARANTINE",
                        locator,
                        key_rule.rule_id,
                        fields,
                        rejected_group[0],
                    )
    return {
        "version": "partial-key-safety-v1",
        "rule_version": profile.version,
        "status": "BLOCKED" if blockers else "PASSED",
        "blockers": blockers,
    }
