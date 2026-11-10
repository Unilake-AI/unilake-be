from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.modules.ingestion.contracts import BronzeBatch, FileProvenance, MappingBinding
from app.modules.ingestion.readers.file_snapshot import fingerprint
from app.modules.pipeline.cleaning_pipeline import CleaningTask
from app.modules.pipeline.duplicate_records import build_duplicate_profile
from app.modules.pipeline.format_validation import SourceFormatOptions, build_format_profile
from app.modules.pipeline.mapping_profiles import pointer
from app.modules.pipeline.missing_values import MissingValueProfile, MissingValueRule


def _demo_cleaning_task(domain: str) -> CleaningTask:
    case = _case(domain)
    source_row = case["records"][0]
    source_columns = tuple(source_row)
    contract = _read_json(f"docs/standardization/silver-schemas/{domain}.json")
    schema = _read_json(f"docs/data-schema/{domain}_v1.schema.json")
    target_to_source = {
        target: paths[0].removeprefix("/") for target, paths in case["field_paths"].items()
    }
    key = MappingBinding(
        source_system_id="17",
        entity=case["entity"],
        source_schema_version="1",
        mapping_id=f"manual-{domain}-csv",
        mapping_version="1",
        target_schema_version=contract["x-version"],
    )
    provenance = FileProvenance(
        source_system_id="17",
        data_source_id="21",
        dataset_id="31",
        source_type="CSV",
        source_ref=f"s3://{settings.minio_bucket_bronze}/{domain}/2026/10/19/job-42/input.csv",
        object_version_id="bronze-version",
        content_sha256="a" * 64,
        parser_fingerprint="demo-parser",
        columns_fingerprint=fingerprint(list(source_columns)),
        captured_at="2026-10-19T00:00:00+00:00",
        locator_kind="parsed_record_ordinal",
    )
    batch = BronzeBatch(
        batch_id="job-42",
        domain_id=domain,
        source_type="CSV",
        records=(source_row,),
        metadata={"input_version": provenance.input_version},
        mapping_binding=MappingBinding(
            key.source_system_id,
            key.entity,
            key.source_schema_version,
            key.mapping_id,
            key.mapping_version,
            key.target_schema_version,
        ),
        record_locators=("record:1",),
        source_paths=tuple(pointer(name) for name in source_columns),
        provenance=provenance,
    )
    entity_schema = schema["entities"][case["entity"]]
    rules = tuple(
        MissingValueRule(
            target_to_source[column["name"]],
            nullable=column["nullable"],
        )
        for column in entity_schema["columns"]
        if column["name"] in target_to_source
    )
    return CleaningTask(
        batch,
        MissingValueProfile(
            domain,
            case["entity"],
            PROFILE_VERSION,
            tuple(target_to_source.values()),
            rules,
        ),
        build_format_profile(
            schema,
            domain=domain,
            entity=case["entity"],
            version=PROFILE_VERSION,
            target_to_source=target_to_source,
            source_options=_format_options(domain, case),
        ),
        build_duplicate_profile(
            schema,
            domain=domain,
            entity=case["entity"],
            version=PROFILE_VERSION,
            target_to_source=target_to_source,
        ),
    )


ROOT = Path(__file__).resolve().parents[2]
PROFILE_VERSION = "test-cleaning-v1"


def _read_json(relative_path: str) -> Any:
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


def _case(domain: str) -> dict[str, Any]:
    cases = _read_json("data/standardization/s2/examples.json")
    selected = next(
        (
            item
            for item in cases
            if item.get("id") == f"{domain}-flat" and item.get("domain") == domain
        ),
        None,
    )
    if not isinstance(selected, dict):
        raise ValueError("DEMO_PROFILE_NOT_FOUND")
    return selected


def _format_options(domain: str, case: dict[str, Any]) -> dict[str, SourceFormatOptions]:
    aliases = _read_json("data/standardization/s4/preview-profiles.json")["profiles"][
        case["entity"]
    ]["dictionaries"]
    options = {
        field: SourceFormatOptions(aliases=tuple(values.items()), trim=True)
        for field, values in aliases.items()
    }
    if domain == "admissions":
        options["max_score"] = SourceFormatOptions(decimal_separator=",", trim=True)
        options["is_active"] = SourceFormatOptions(
            true_tokens=("Có", "true"),
            false_tokens=("Không", "false"),
            trim=True,
        )
    elif domain == "academic":
        for field in ("start_date", "end_date"):
            options[field] = SourceFormatOptions(date_formats=("%d/%m/%Y",), trim=True)
    return options
