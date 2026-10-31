"""Bản xem trước S2 → S3 giả lập; mã hóa tường minh kiểu gốc cho đầu ra JSON."""

from __future__ import annotations

import argparse
import io
import json
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal

from preview_s2 import ROOT, preview_case

from app.modules.pipeline.type_standardization import (
    FieldOptions,
    TypeStandardizationService,
)


def encode(value):
    if isinstance(value, Decimal):
        return {"type": "decimal", "value": str(value)}
    if isinstance(value, datetime):
        return {"type": "timestamp_utc", "value": value.isoformat()}
    if isinstance(value, date):
        return {"type": "date", "value": value.isoformat()}
    raise TypeError(type(value).__name__)


def main():
    import sys

    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", default="admissions-flat")
    args = parser.parse_args()
    mapped = preview_case(args.case)
    contract = json.loads(
        (
            ROOT / f"docs/standardization/silver-schemas/{mapped.key.domain_id}.json"
        ).read_text(encoding="utf-8")
    )
    options = {}
    if mapped.key.entity == "admission_method":
        options = {
            "max_score": FieldOptions(decimal_separator=","),
            "is_active": FieldOptions(true_tokens=("Có",), false_tokens=("Không",)),
        }
    if mapped.key.entity == "semester":
        options = {
            name: FieldOptions(date_format="%d/%m/%Y")
            for name in ("start_date", "end_date")
        }
    service = TypeStandardizationService(
        key=mapped.key,
        conversion_version="synthetic-types-1",
        contract=contract,
        options=options,
    )
    print(
        json.dumps(
            asdict(service.standardize(mapped)),
            ensure_ascii=False,
            indent=2,
            default=encode,
        )
    )


if __name__ == "__main__":
    main()
