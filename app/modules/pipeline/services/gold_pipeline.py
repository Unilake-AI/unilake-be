"""Đọc đúng bản Silver đã công bố, tổng hợp và công bố Gold cùng bằng chứng nguồn gốc."""

from __future__ import annotations

import json
from dataclasses import asdict
from io import BytesIO
from typing import TYPE_CHECKING, Any

import pyarrow.parquet as pq

from app.core.config import settings
from app.modules.governance.contracts import (
    AssetReference,
    DatasetDerivation,
    LineageEvidence,
    TransformationEvidence,
)
from app.modules.pipeline.repositories.silver_storage import ObjectRef, checksum
from app.modules.pipeline.services.gold_engine import (
    RECIPE_FINGERPRINT,
    RECIPE_VERSION,
    aggregate,
    parquet_rows,
)

if TYPE_CHECKING:
    from app.modules.pipeline.repositories.gold import GoldRepository
    from app.modules.pipeline.repositories.silver_storage import SilverObjectStore


def build_gold(
    job: dict[str, Any],
    silver: SilverObjectStore,
    gold: SilverObjectStore,
    repository: GoldRepository,
) -> bool:
    if job["recipe_version"] != RECIPE_VERSION or job["recipe_fingerprint"] != RECIPE_FINGERPRINT:
        raise ValueError("GOLD_RECIPE_VERSION_UNSUPPORTED")
    ref = job["silver_manifest_ref"]
    source_ref = ObjectRef(**(json.loads(ref) if isinstance(ref, str) else ref))
    manifest = json.loads(silver.get(source_ref))
    if (
        manifest.get("snapshot_id") != job["silver_snapshot_id"]
        or manifest.get("scope") != job["scope"]
        or manifest.get("mode") != "production"
        or manifest.get("format") != "unilake-silver-v1"
        or manifest.get("activation_allowed") is not True
    ):
        raise ValueError("GOLD_SILVER_MANIFEST_INVALID")
    rows: list[dict[str, Any]] = []
    source_objects = []
    total_records = 0
    total_bytes = 0
    for entity, artifact in manifest["entities"].items():
        # Không gộp ngầm nhiều bộ dữ liệu hoặc lĩnh vực thành một báo cáo.
        batches = artifact.get("lineage", [])
        if not batches:
            raise ValueError("GOLD_SOURCE_EVIDENCE_REQUIRED")
        for batch in batches:
            provenance = batch.get("provenance") or {}
            if batch["key"]["domain_id"] != job["domain"] or str(
                provenance.get("dataset_id")
            ) != str(job["dataset_id"]):
                raise ValueError("GOLD_MIXED_SOURCE_SCOPE_UNSUPPORTED")
        object_ref = ObjectRef(**artifact["object"])
        total_bytes += object_ref.size
        if total_bytes > 200 * 1024 * 1024 or artifact["rows"] + total_records > 1_000_000:
            raise ValueError("GOLD_INPUT_LIMIT_EXCEEDED")
        # Kho đọc kiểm tra mã nội dung và phiên bản trước khi trả dữ liệu.
        data = silver.get(object_ref)
        if pq.read_metadata(BytesIO(data)).num_rows != artifact["rows"]:
            raise ValueError("GOLD_SILVER_ROW_COUNT_MISMATCH")
        table = pq.read_table(BytesIO(data))
        if table.num_rows != artifact["rows"]:
            raise ValueError("GOLD_SILVER_ROW_COUNT_MISMATCH")
        total_records += table.num_rows
        if total_records > 1_000_000:
            raise ValueError("GOLD_INPUT_LIMIT_EXCEEDED")
        rows.extend(aggregate(job["domain"], entity, table))
        source_objects.append(
            {"entity": entity, "object": asdict(object_ref), "rows": table.num_rows}
        )
    prefix = "gold/production/" + checksum(job["scope"].encode()) + "/" + job["job_id"]
    output = BytesIO()
    pq.write_table(parquet_rows(rows), output, compression="zstd")
    data = output.getvalue()
    artifact_ref = gold.put(prefix + "/aggregate-" + checksum(data) + ".parquet", data)
    lineage = LineageEvidence(
        run_ref=job["job_id"],
        mode="production",
        parent_snapshot=None,
        derivations=(
            DatasetDerivation(
                inputs=(
                    AssetReference(
                        asset_id="silver:" + job["scope"],
                        layer="SILVER",
                        domain=job["domain"],
                        version=job["silver_snapshot_id"],
                        uri=f"s3://{settings.minio_bucket_silver}/" + source_ref.key,
                        checksum=source_ref.sha256,
                    ),
                ),
                output=AssetReference(
                    asset_id="gold:" + job["scope"],
                    layer="GOLD",
                    domain=job["domain"],
                    version=job["job_id"],
                    uri=f"s3://{settings.minio_bucket_gold}/" + artifact_ref.key,
                    checksum=artifact_ref.sha256,
                    producer_run_ref=job["job_id"],
                ),
                transformations=(
                    TransformationEvidence(
                        name="GROUPED_RECORD_COUNTS",
                        version=RECIPE_VERSION,
                        fingerprint=RECIPE_FINGERPRINT,
                    ),
                ),
            ),
        ),
    ).model_dump(mode="json")
    result = {
        "format": "unilake-gold-v1",
        "gold_snapshot_id": job["job_id"],
        "scope": job["scope"],
        "generation": job["generation"],
        "domain": job["domain"],
        "dataset_id": job["dataset_id"],
        "silver_snapshot_id": job["silver_snapshot_id"],
        "silver_manifest": asdict(source_ref),
        "source_objects": source_objects,
        "recipe_version": RECIPE_VERSION,
        "recipe_fingerprint": RECIPE_FINGERPRINT,
        "records_read": total_records,
        "aggregate_rows": len(rows),
        "object": asdict(artifact_ref),
        "lineage": lineage,
    }
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True).encode()
    stored = gold.put(prefix + "/manifest-" + checksum(encoded) + ".json", encoded)
    # Chỉ công bố sau khi đọc lại và xác minh cả bảng kết quả lẫn bản kê đã lưu.
    gold.get(artifact_ref)
    gold.get(stored)
    return repository.publish(job, asdict(stored), result, rows)
