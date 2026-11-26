"""Bind a business import to the existing immutable Bronze/Silver pipeline."""

from __future__ import annotations

from datetime import UTC
from typing import Any

from app.modules.ingestion.contracts.business_imports import (
    ImportOptions,
    ImportValidationError,
    contract_for,
    detect_contract,
    fingerprint,
    normalize_row,
    packed,
    resolve_layout,
    supported_contract_fingerprints,
)
from app.modules.pipeline.contracts.automation import AUTOMATIC_RECIPE_VERSION
from app.modules.pipeline.services.source_profiles import SourceProfile
from app.modules.pipeline.services.source_schema_builder import generic_schema


def build_import_profile(
    context: dict[str, Any], parsed: Any, contracts: dict[str, Any]
) -> SourceProfile | None:
    upload_input = context.get("upload_input") or {}
    supplied = upload_input.get("business_import")
    options = (
        ImportOptions.model_validate(supplied)
        if supplied
        else detect_contract(context["domain"], parsed.columns)
    )
    if options is None:
        return None
    contract = contract_for(options, context["domain"])
    current_fingerprint = fingerprint(contract.describe())
    pinned = upload_input.get("business_import_contract_fingerprint") or current_fingerprint
    if pinned not in supported_contract_fingerprints(contract):
        raise ImportValidationError([{"code": "IMPORT_CONTRACT_VERSION_CHANGED"}])
    mapping = resolve_layout(contract, parsed.columns, options)
    # Operational personal data stays in the separate business artifact.
    analytic = {
        f.name: mapping[f.name]
        for f in contract.fields
        if f.name in mapping and f.kind == "string" and not f.private
    }
    audit = {
        n: s
        for n, s in contracts["hr"]["$defs"]["position"]["properties"].items()
        if n.startswith("_")
    }
    source, schema = generic_schema(
        context["domain"], contract.entity, analytic, {}, set(), set(), [], audit
    )
    dictionaries = {
        field.name: aliases
        for field in contract.fields
        if field.name in analytic and (aliases := field.accepted_values)
    }
    for name, aliases in dictionaries.items():
        prop = schema["$defs"][contract.entity]["properties"][name]
        prop["enum"] = sorted(set(aliases.values())) + ([None] if "null" in prop["type"] else [])
    evidence = {
        "options": options.metadata(),
        "field_mapping": mapping,
        "columns": parsed.columns,
        "contract_fingerprint": pinned,
        "layout_fingerprint": fingerprint(
            {"columns": parsed.columns, "parser": context["parser_options"]}
        ),
    }
    scope = (
        "auto-"
        + fingerprint(
            {
                "dataset": context["dataset_id"],
                "source": context["data_source_id"],
                "table": context["source_table"],
            }
        )[:28]
    )
    return SourceProfile(
        data_source_id=context["data_source_id"],
        dataset_id=context["dataset_id"],
        source_system_id=context["source_system_id"],
        source_type=context["source_type"],
        source_table=context["source_table"],
        domain=context["domain"],
        entity=contract.entity,
        version=AUTOMATIC_RECIPE_VERSION,
        scope=scope,
        source_schema_version=source["version"],
        source_schema=source,
        contracts={context["domain"]: schema},
        field_mapping=analytic,
        ignored_columns=[c for c in parsed.columns if c not in analytic.values()],
        format_options={name: {"trim": True, "unicode_nfc": True} for name in analytic},
        dictionaries=dictionaries,
        identity_mode="source",
        effective_at=context["created_at"].replace(tzinfo=UTC),
        business_import=evidence,
    )


def operational_fields(profile: SourceProfile, batch: Any) -> dict[str, dict[str, dict[str, str]]]:
    evidence = profile.business_import
    if evidence is None:
        return {}
    options = ImportOptions.model_validate(evidence["options"])
    contract = contract_for(options, profile.domain)
    if evidence["contract_fingerprint"] not in supported_contract_fingerprints(contract):
        raise ValueError("IMPORT_CONTRACT_VERSION_CHANGED")
    mapping = evidence["field_mapping"]
    result = {}
    errors = []
    for locator, raw in zip(batch.record_locators, batch.records, strict=True):
        try:
            values = normalize_row(contract, raw, mapping, options, locator)
        except ImportValidationError as exc:
            errors.extend(exc.issues)
            continue
        result[locator] = {
            "_business_import_row": packed(
                {
                    "domain": profile.domain,
                    "namespace": str(profile.source_system_id),
                    "locator": locator,
                    "values": values,
                }
            )
        }
    if errors:
        raise ImportValidationError(errors)
    return {profile.entity: result}


def mapped_person_name(profile: SourceProfile, record: dict[str, Any]) -> str | None:
    # Legacy profiles commonly omit full_name from analytic field_mapping.
    source = profile.field_mapping.get("full_name")
    if source:
        value = record.get(source)
    else:
        from app.modules.ingestion.contracts.business_imports import PERSON, header

        aliases = {header(n) for n in (PERSON[0].name, *PERSON[0].aliases)}
        matches = [v for k, v in record.items() if header(k) in aliases]
        value = matches[0] if len(matches) == 1 else None
    return value.strip() if isinstance(value, str) and value.strip() else None
