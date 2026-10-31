"""Làm mới danh mục nguồn P0; không sửa schema nguồn hoặc dữ liệu mẫu cũ."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs/standardization"


def main() -> None:
    evidence = OUT / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    inventory = []
    fields = []
    links = []
    hashes = {}
    for path in sorted((ROOT / "docs/data-schema").glob("*")):
        if path.suffix in {"json", ".json", ".sql", ".txt"}:
            hashes[path.relative_to(ROOT).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    for domain in ("admissions", "academic", "hr"):
        path = ROOT / f"docs/data-schema/{domain}_v1.schema.json"
        schema = json.loads(path.read_text(encoding="utf-8"))
        for entity, spec in schema["entities"].items():
            if "from_dataset" in spec:
                continue
            sample = ROOT / "data/samples" / spec["file"]
            header = None
            if sample.exists():
                with sample.open(encoding="utf-8-sig", newline="") as stream:
                    header = next(csv.reader(stream))
            expected = [col["name"] for col in spec["columns"]]
            inventory.append(
                {
                    "domain": domain,
                    "entity": entity,
                    "version": schema["version"],
                    "primary_key": spec["primary_key"],
                    "columns": len(expected),
                    "sample": spec["file"],
                    "sample_exists": sample.exists(),
                    "header_matches": header == expected,
                    "missing_columns": sorted(set(expected) - set(header or [])),
                    "extra_columns": sorted(set(header or []) - set(expected)),
                }
            )
            for col in spec["columns"]:
                name = col["name"]
                direct = name in spec.get("pii_columns", [])
                free = col["db_type"] == "text"
                identity = name.endswith("_id") or name in {
                    "student_code",
                    "employee_code",
                }
                quasi = name in {"gender", "province", "high_school_name"}
                action = (
                    "DROP_FROM_ANALYTICS"
                    if direct or free
                    else "KEEP_RESTRICTED"
                    if identity or quasi
                    else "KEEP"
                )
                fields.append(
                    [
                        domain,
                        entity,
                        name,
                        col["type"],
                        col["nullable"],
                        "DIRECT_PII"
                        if direct
                        else "FREE_TEXT_REVIEW"
                        if free
                        else "LINKABLE"
                        if identity or quasi
                        else "DOMAIN_DATA",
                        action,
                        "REDACT"
                        if direct or free or identity or quasi
                        else "CODE_AND_LOCATOR_ONLY",
                        "PROPOSED",
                        "Data Administrator + domain owner",
                    ]
                )
                ref = col.get("references")
                if ref:
                    links.append(
                        [
                            domain,
                            entity,
                            name,
                            ref["entity"],
                            ref["column"],
                            col["nullable"],
                        ]
                    )
    (evidence / "schema-inventory.json").write_text(
        json.dumps(
            {"baseline_date": "2026-10-14", "hashes": hashes, "entities": inventory},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    # Tách riêng việc duyệt chính sách của con người: đây là bản trích xuất, không phải ma trận chính sách để chỉnh sửa.
    for filename, headers, rows in [
        (
            "pii-matrix.csv",
            [
                "domain",
                "entity",
                "field",
                "source_type",
                "source_nullable",
                "classification",
                "proposed_silver_action",
                "log_policy",
                "approval_status",
                "review_owner",
            ],
            fields,
        ),
        (
            "cross-domain-links.csv",
            [
                "owner_domain",
                "entity",
                "field",
                "target_entity",
                "target_field",
                "nullable",
            ],
            links,
        ),
    ]:
        target = OUT / filename
        if target.exists():
            target = evidence / ("generated-" + filename)
        with target.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(headers)
            writer.writerows(rows)
    lines = [
        "# Scope matrix P0",
        "",
        "Nguồn được pin bằng SHA-256 tại `evidence/schema-inventory.json`. Đây là inventory nguồn, chưa phải Silver schema được duyệt.",
        "",
        "| Domain | Entity (một dòng theo PK) | Version nguồn | PK | Sample legacy |",
        "|---|---|---|---|---|",
    ]
    for item in inventory:
        state = (
            "Khớp header"
            if item["header_matches"]
            else "Thiếu file"
            if not item["sample_exists"]
            else "Lệch header"
        )
        lines.append(
            f"| {item['domain']} | {item['entity']} | {item['version']} | {', '.join(item['primary_key'])} | {state} |"
        )
    lines.extend(
        [
            "",
            "Ownership theo schema hiện tại: Admissions 6, Academic 9, HR 7. Entity tham chiếu không được tính thêm hoặc tạo bản sao owner. `cross-domain-links.csv` liệt kê toàn bộ FK, gồm cả quan hệ nội domain; đây là structural mapping, không phải crosswalk mã giữa nguồn.",
            "",
            "Trong phạm vi: 22 entity, cấu trúc/kiểu/ID/enum, lỗi và lineage, công bố Silver. Ngoài phạm vi story: Gold/KPI/NLQ, đồng bộ hai chiều về hệ nguồn và fuzzy matching tự động.",
            "",
            "S1 phải chốt physical type, PII projection, enum/precision/timezone, nullable/unique và mapping từng field. Không coi số dòng hoặc header đúng là bằng chứng dữ liệu hợp lệ.",
        ]
    )
    (OUT / "scope-matrix.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        f"Inventory: {len(inventory)} entities, {len(fields)} fields, {len(links)} foreign keys"
    )
    print(
        f"Legacy headers: {sum(x['header_matches'] for x in inventory)} match; {sum(not x['sample_exists'] for x in inventory)} missing files"
    )


if __name__ == "__main__":
    main()
