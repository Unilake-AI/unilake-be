"""Chạy bản xem trước S2 giả lập mà không ghi kho lưu trữ hay cơ sở dữ liệu."""

from __future__ import annotations

# Script chạy trực tiếp cần thêm backend vào sys.path trước khi import ứng dụng.
# ruff: noqa: E402
import argparse
import io
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.modules.ingestion.contracts import BronzeBatch, MappingBinding
from app.modules.pipeline.mapping_profiles import build_profile
from app.modules.pipeline.schema_mapping import (
    MappingKey,
    MappingRegistry,
    SchemaMappingService,
)


def map_case(case_id, *, preview_limit=None):
    cases = json.loads(
        (ROOT / "data/standardization/s2/examples.json").read_text(encoding="utf-8")
    )
    case = next((c for c in cases if c["id"] == case_id), None)
    if case is None:
        raise ValueError("Unknown case: " + case_id)
    schema = json.loads(
        (ROOT / f"docs/standardization/silver-schemas/{case['domain']}.json").read_text(
            encoding="utf-8"
        )
    )
    key = MappingKey(
        "SYNTH_" + case["domain"],
        case["domain"],
        case["entity"],
        "synthetic-1",
        case["id"],
        "1",
        schema["x-version"],
    )
    target = schema["$defs"][case["entity"]]["properties"]
    profile = build_profile(
        key=key,
        source_type="JSON",
        target_fields=tuple(target),
        source_columns=tuple(case["source_paths"]),
        field_paths={k: tuple(v) for k, v in case["field_paths"].items()},
        ignored_sources=tuple(case["ignored_sources"]),
        deferred_fields=tuple(n for n in target if n not in case["field_paths"]),
    )
    batch = BronzeBatch(
        "s2-example",
        case["domain"],
        "JSON",
        tuple(case["records"]),
        metadata={"input_version": "fixture-v1"},
        mapping_binding=MappingBinding(
            key.source_system_id,
            key.entity,
            key.source_schema_version,
            key.mapping_id,
            key.mapping_version,
            key.target_schema_version,
        ),
        source_paths=tuple(case["source_paths"]),
        record_locators=tuple(
            f"fixture-v1:/records/{i}" for i in range(len(case["records"]))
        ),
    )
    return SchemaMappingService(MappingRegistry((profile,))).map_batch(
        batch, preview_limit=preview_limit
    )


def preview_case(case_id, limit=20):
    return map_case(case_id, preview_limit=limit)


def main():
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", default="admissions-flat")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    try:
        result = preview_case(args.case, args.limit)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
