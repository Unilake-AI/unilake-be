from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.modules.governance.contracts import LineageEvidence
from app.modules.pipeline.lineage import silver_lineage


def evidence(verified=True):
    return {
        "key": {
            "source_system_id": "1",
            "domain_id": "hr",
            "entity": "employee",
            "mapping_version": "map-v2",
        },
        "input_version": "sha256:input",
        "conversion_version": "types-v3",
        "conversion_fingerprint": "types-hash",
        "identity_version": "identity-v4",
        "configuration_fingerprint": "identity-hash",
        "processing_steps": (("CLEAN_MISSING", "missing-v1"),),
        "provenance": {
            "source_system_id": "1",
            "data_source_id": "7",
            "dataset_id": "42",
            "ingestion_job_id": 101 if verified else None,
            "object_version_id": "bronze-v7",
            "source_ref": "s3://bronze/object",
            "content_sha256": "input-hash",
        },
    }


def build(batch):
    return silver_lineage(
        run_id="snapshot-1",
        scope="demo",
        mode="rehearsal",
        parent="snapshot-0",
        updated={"employee": [batch]},
        entities={
            "employee": {"object": {"sha256": "output-hash"}},
            "department": {"object": {"sha256": "inherited-hash"}},
        },
    )


def test_exact_upstream_version_steps_and_no_inherited_or_gold_edges():
    batch = evidence()
    before = deepcopy(batch)
    result = LineageEvidence.model_validate(build(batch))
    assert batch == before
    assert result.parent_snapshot == "snapshot-0"
    assert len(result.derivations) == 1
    edge = result.derivations[0]
    assert edge.inputs[0].asset_id == "dataset:42"
    assert edge.inputs[0].version == "bronze-v7"
    assert edge.inputs[0].producer_run_ref == "ingestion:101"
    assert edge.output.version == "snapshot-1"
    assert edge.output.layer == "SILVER"
    assert [step.name for step in edge.transformations] == [
        "CLEAN_MISSING",
        "SCHEMA_MAPPING",
        "TYPE_CONVERSION",
        "IDENTITY_NORMALIZATION",
    ]


def test_s3_file_without_verified_job_is_not_claimed_as_bronze():
    batch = evidence(verified=False)
    batch["processing_steps"] = ()
    edge = build(batch)["derivations"][0]
    assert edge["inputs"][0]["layer"] is None
    assert edge["inputs"][0]["producer_run_ref"] is None
    assert len(edge["transformations"]) == 3


def test_contract_rejects_empty_evidence_and_unknown_fields():
    payload = build(evidence())
    with pytest.raises(ValidationError):
        LineageEvidence.model_validate({**payload, "derivations": []})
    with pytest.raises(ValidationError):
        LineageEvidence.model_validate({**payload, "password": "forbidden"})


@pytest.mark.parametrize("job_id", [0, -1, True, "101"])
def test_invalid_bronze_job_reference_is_rejected(job_id):
    batch = evidence()
    batch["provenance"]["ingestion_job_id"] = job_id
    with pytest.raises(ValueError, match="BRONZE_ORIGIN_EVIDENCE_INCOMPLETE"):
        build(batch)


@pytest.mark.parametrize("version", [None, "", "null", "   "])
def test_published_layer_requires_real_version(version):
    payload = build(evidence())
    payload["derivations"][0]["output"]["version"] = version
    with pytest.raises(ValidationError):
        LineageEvidence.model_validate(payload)
