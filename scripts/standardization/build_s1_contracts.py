"""Tạo hợp đồng Silver có thể rà soát từ schema nguồn đã cố định (không phải bộ xử lý ETL)."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs/standardization"
VERSION = "0.1.0-draft"
AUDIT = {
    "_source_system_id": "string",
    "_source_record_id": "string",
    "_batch_id": "string",
    "_run_id": "string",
    "_processed_at": "string",
    "_schema_version": "string",
    "_mapping_version": "string",
}


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def field_schema(col: dict[str, Any]) -> dict[str, Any]:
    sql: str = col["type"]
    base = sql.split("(")[0]
    obj: dict[str, Any] = {"type": "string", "x-physical-type": sql}
    if base in ("bigint", "int"):
        bits = 64 if base == "bigint" else 32
        obj.update(
            type="integer", minimum=-(2 ** (bits - 1)), maximum=2 ** (bits - 1) - 1
        )
    elif base == "boolean":
        obj["type"] = "boolean"
    elif base == "decimal":
        precision, scale = map(int, sql[8:-1].split(","))
        obj.update(
            pattern=rf"^-?(?:0|[1-9][0-9]{{0,{precision-scale-1}}})\.[0-9]{{{scale}}}$",
            **{
                "x-precision": precision,
                "x-scale": scale,
                "x-rounding": "reject_excess_scale",
            },
        )
    elif base == "date":
        obj.update(format="date", **{"x-physical-type": "date32"})
    elif base == "timestamp":
        obj.update(
            format="date-time", pattern="Z$", **{"x-physical-type": "timestamp[us,UTC]"}
        )
    elif base == "varchar":
        obj["maxLength"] = int(sql[8:-1])
    for key in ("pattern", "enum", "description"):
        if key in col:
            obj[key] = col[key]
    for source, target in (("min", "minimum"), ("max", "maximum")):
        if source in col:
            obj[target if base in ("int", "bigint") else "x-" + target] = col[source]
    if col["nullable"]:
        obj["type"] = [obj["type"], "null"]
        if "enum" in obj:
            obj["enum"] = [*obj["enum"], None]
    if col.get("unique"):
        obj["x-unique-non-null"] = True
    if col.get("references"):
        obj["x-references"] = col["references"]
    return obj


def main():
    mappings, links, inventory, dq = [], [], [], []
    for domain in ("admissions", "academic", "hr"):
        source = ROOT / f"docs/data-schema/{domain}_v1.schema.json"
        data = json.loads(source.read_text(encoding="utf-8"))
        doc = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"urn:unilake:silver:{domain}:{VERSION}",
            "title": f"{domain} Silver snapshot",
            "type": "object",
            "additionalProperties": False,
            "required": [],
            "properties": {},
            "$defs": {},
            "x-version": VERSION,
            "x-approval": "PENDING_DOMAIN_AND_PII_REVIEW",
            "x-source-version": data["version"],
            "x-source-sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "x-publish-policy": "strict_snapshot",
            "x-json-decimal-encoding": "string",
        }
        for entity, spec in data["entities"].items():
            if "columns" not in spec:
                continue
            row = {
                "type": "object",
                "additionalProperties": False,
                "required": [],
                "properties": {},
                "x-grain": spec["description"],
                "x-owner": domain,
                "x-primary-key": spec["primary_key"],
                "x-unique-together": spec.get("unique_together", []),
                "x-identity-mode": "composite_fk"
                if len(spec["primary_key"]) > 1
                else "shared_fk"
                if entity == "lecturer"
                else "canonical_surrogate",
            }
            for col in spec["columns"]:
                name = col["name"]
                drop = name in spec.get("pii_columns", []) or col["db_type"] == "text"
                rule = (
                    "PII-01"
                    if drop
                    else "ID-02"
                    if col.get("references")
                    else "ID-01"
                    if name in spec["primary_key"]
                    else "VAL-01"
                    if "enum" in col
                    else "TYPE-01"
                )
                mappings.append(
                    [
                        domain,
                        entity,
                        name,
                        "" if drop else name,
                        "DROP"
                        if drop
                        else "RESOLVE_FK"
                        if col.get("references")
                        else "RESOLVE_ID"
                        if rule == "ID-01"
                        else "CONVERT",
                        rule,
                        data["version"],
                        VERSION,
                        "DRAFT",
                    ]
                )
                if drop:
                    continue
                row["properties"][name] = field_schema(col)
                row["required"].append(
                    name
                )  # Cho phép null và thiếu trường là hai trường hợp khác nhau.
                if col.get("references"):
                    links.append(
                        [
                            domain,
                            entity,
                            name,
                            col["references"]["entity"],
                            col["references"]["column"],
                            col["nullable"],
                        ]
                    )
            if entity in ("student", "employee"):
                row["properties"]["birth_year"] = {
                    "type": ["integer", "null"],
                    "minimum": 1,
                    "maximum": 9999,
                    "x-physical-type": "int32",
                    "x-derived-from": "date_of_birth",
                    "x-rule": "PII-02",
                }
                row["required"].append("birth_year")
                mappings.append(
                    [
                        domain,
                        entity,
                        "date_of_birth",
                        "birth_year",
                        "DERIVE_YEAR_BEFORE_DROP",
                        "PII-02",
                        data["version"],
                        VERSION,
                        "PROPOSED",
                    ]
                )
            for name, typ in AUDIT.items():
                row["properties"][name] = {
                    "type": typ,
                    "minLength": 1,
                    "x-physical-type": "utf8",
                }
                if name == "_processed_at":
                    row["properties"][name].update(
                        format="date-time",
                        pattern="Z$",
                        **{"x-physical-type": "timestamp[us,UTC]"},
                    )
                if name == "_schema_version":
                    row["properties"][name]["const"] = VERSION
                row["required"].append(name)
                mappings.append(
                    [
                        domain,
                        entity,
                        "handoff." + name,
                        name,
                        "AUDIT",
                        "AUDIT-01",
                        data["version"],
                        VERSION,
                        "DRAFT",
                    ]
                )
            doc["$defs"][entity] = row
            doc["properties"][entity] = {
                "type": "array",
                "items": {"$ref": "#/$defs/" + entity},
            }
            doc["required"].append(entity)
            inventory.append(
                {
                    "domain": domain,
                    "entity": entity,
                    "primary_key": spec["primary_key"],
                    "source_version": data["version"],
                }
            )
        # Giữ mọi quy tắc nghiệp vụ nguồn thành mục cần duyệt, không âm thầm triển khai kiểm tra.
        dq.extend(
            {
                "domain": domain,
                **r,
                "implementation": "NOT_IMPLEMENTED",
                "review": "REQUIRES_DOMAIN_REVIEW",
                "evaluation_stage": "restricted_source_or_silver_as_applicable",
            }
            for r in data.get("quality_rules", [])
        )
        write(OUT / f"silver-schemas/{domain}.json", doc)
    for filename, header, rows in (
        (
            "mapping-matrix.csv",
            [
                "domain",
                "entity",
                "source_field",
                "target_field",
                "operation",
                "rule_id",
                "source_version",
                "target_version",
                "status",
            ],
            mappings,
        ),
        (
            "silver-cross-domain-links.csv",
            ["domain", "entity", "field", "target_entity", "target_field", "nullable"],
            links,
        ),
    ):
        with (OUT / filename).open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(header)
            writer.writerows(rows)
    write(OUT / "business-rule-review.json", dq)
    write(
        OUT / "silver-schemas/manifest.json",
        {
            "version": VERSION,
            "entities": inventory,
            "activation_allowed": False,
            "blockers": ["G0", "D04_PII", "D07_AUTHORITY", "DOMAIN_RULE_REVIEW"],
            "audit_fields": list(AUDIT),
        },
    )
    print(
        f"Built {len(inventory)} entities; {len(mappings)} mappings; {len(links)} FK; {len(dq)} business rules for review"
    )


if __name__ == "__main__":
    main()
