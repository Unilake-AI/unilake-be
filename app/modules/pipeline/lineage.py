"""Build dataset evidence from the exact batches being published, never from fixtures."""

from __future__ import annotations

from typing import Any

from app.modules.governance.contracts import (
    AssetReference,
    DatasetDerivation,
    LineageEvidence,
    TransformationEvidence,
)


def silver_lineage(
    *,
    run_id: str,
    scope: str,
    mode: str,
    parent: str | None,
    updated: dict[str, list[dict[str, Any]]],
    entities: dict[str, Any],
) -> dict[str, Any]:
    if mode not in {"rehearsal", "production"}:
        raise ValueError("INVALID_LINEAGE_MODE")
    derivations = []
    for entity, batches in updated.items():
        for batch in batches:
            key = batch["key"]
            provenance = batch.get("provenance") or {}
            verified_bronze = provenance.get("ingestion_job_id") is not None
            if verified_bronze and (
                type(provenance["ingestion_job_id"]) is not int
                or provenance["ingestion_job_id"] <= 0
                or not provenance.get("dataset_id")
                or not provenance.get("object_version_id")
                or provenance["object_version_id"] == "null"
                or not provenance.get("content_sha256")
                or not str(provenance.get("source_ref", "")).startswith("s3://")
            ):
                raise ValueError("BRONZE_ORIGIN_EVIDENCE_INCOMPLETE")
            steps = [
                TransformationEvidence(name=name, version=version)
                for name, version in batch.get("processing_steps", ())
            ]
            steps.extend(
                (
                    TransformationEvidence(name="SCHEMA_MAPPING", version=key["mapping_version"]),
                    TransformationEvidence(
                        name="TYPE_CONVERSION",
                        version=batch["conversion_version"],
                        fingerprint=batch["conversion_fingerprint"],
                    ),
                    TransformationEvidence(
                        name="IDENTITY_NORMALIZATION",
                        version=batch["identity_version"],
                        fingerprint=batch["configuration_fingerprint"],
                    ),
                )
            )
            artifact = entities[entity]["object"]
            derivations.append(
                DatasetDerivation(
                    inputs=(
                        AssetReference(
                            asset_id=(
                                f"dataset:{provenance['dataset_id']}"
                                if verified_bronze
                                else (
                                    f"input:{provenance['source_system_id']}:{provenance['data_source_id']}:{provenance['dataset_id']}"
                                    if provenance
                                    else f"input:{key['source_system_id']}:{entity}"
                                )
                            ),
                            layer="BRONZE" if verified_bronze else None,
                            domain=key["domain_id"],
                            version=provenance.get("object_version_id") or batch["input_version"],
                            uri=provenance.get("source_ref"),
                            checksum=provenance.get("content_sha256"),
                            producer_run_ref=(
                                f"ingestion:{provenance['ingestion_job_id']}"
                                if verified_bronze
                                else None
                            ),
                        ),
                    ),
                    output=AssetReference(
                        asset_id=f"silver:{scope}:{key['domain_id']}:{entity}",
                        layer="SILVER",
                        domain=key["domain_id"],
                        version=run_id,
                        checksum=artifact["sha256"],
                        producer_run_ref=f"silver:{run_id}",
                    ),
                    transformations=tuple(steps),
                )
            )
    return LineageEvidence(
        run_ref=f"silver:{run_id}",
        mode="production" if mode == "production" else "rehearsal",
        parent_snapshot=parent,
        derivations=tuple(derivations),
    ).model_dump(mode="json")
