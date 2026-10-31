"""Bản xem trước S2 → S3 → S4 giả lập; không cấp phát trong cơ sở dữ liệu hay ghi Silver."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime

from preview_s2 import ROOT, map_case, preview_case
from preview_s3 import encode

from app.modules.pipeline.identity_contracts import CrosswalkEntry, CrosswalkSnapshot
from app.modules.pipeline.identity_standardization import (
    IdentityStandardizationService,
    ValueDictionary,
)
from app.modules.pipeline.type_standardization import (
    FieldOptions,
    TypeStandardizationService,
)


def normalize_case(case_id, *, preview=True):
    mapped = preview_case(case_id) if preview else map_case(case_id)
    contract = json.loads(
        (
            ROOT / f"docs/standardization/silver-schemas/{mapped.key.domain_id}.json"
        ).read_text(encoding="utf-8")
    )
    fixture = json.loads(
        (ROOT / "data/standardization/s4/preview-profiles.json").read_text(
            encoding="utf-8"
        )
    )
    options = {}
    if mapped.key.entity == "admission_method":
        options = {
            "max_score": FieldOptions(decimal_separator=","),
            "is_active": FieldOptions(true_tokens=("Có",), false_tokens=("Không",)),
        }
    if mapped.key.entity == "semester":
        options = {
            n: FieldOptions(date_format="%d/%m/%Y") for n in ("start_date", "end_date")
        }
    types = TypeStandardizationService(
        key=mapped.key,
        conversion_version="synthetic-types-1",
        contract=contract,
        options=options,
    )
    profile = fixture["profiles"][mapped.key.entity]
    when = datetime.fromisoformat(fixture["effective_at"])
    entry = CrosswalkEntry(
        profile["namespace"],
        mapped.key.entity,
        (profile["source_id"],),
        profile["canonical_id"],
        "synthetic-fixture-only",
        when,
    )
    engine = IdentityStandardizationService(
        types=types,
        version=fixture["version"],
        crosswalk=CrosswalkSnapshot(fixture["crosswalk_version"], (entry,)),
        dictionaries={
            name: ValueDictionary("synthetic-dictionary-1", tuple(aliases.items()))
            for name, aliases in profile["dictionaries"].items()
        },
        reference_namespaces={},
        effective_at=when,
    )
    return engine.standardize(types.standardize(mapped))


def preview(case_id):
    return normalize_case(case_id)


def main():
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", default="admissions-flat")
    args = parser.parse_args()
    try:
        result = preview(args.case)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2, default=encode))


if __name__ == "__main__":
    main()
