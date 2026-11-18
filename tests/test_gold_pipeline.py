"""Kiểm tra Gold giữ đúng mã nguồn, số dòng và chỉ tổng hợp từ bản Silver hợp lệ."""

from __future__ import annotations

import json
from dataclasses import asdict
from io import BytesIO
from unittest.mock import Mock
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from app.core.deps import get_current_user
from app.main import app
from app.modules.pipeline.repositories.silver_storage import LocalSilverStore, checksum
from app.modules.pipeline.routers.gold import access
from app.modules.pipeline.services.gold_engine import RECIPE_FINGERPRINT, RECIPE_VERSION, aggregate
from app.modules.pipeline.services.gold_pipeline import build_gold


def test_identifiers_remain_separate_and_null_counts_are_explicit():
    rows = aggregate(
        "academic",
        "student",
        pa.Table.from_pylist(
            [
                {"student_id": "001", "program_id": "007", "gender": "F"},
                {"student_id": "002", "program_id": "7", "gender": None},
                {"student_id": "003", "program_id": "007", "gender": "F"},
            ]
        ),
    )
    programs = [row for row in rows if row["metric"] == "record_count_by_program_id"]
    assert {(row["dimensions"]["program_id"], row["value"]) for row in programs} == {
        ("007", 2),
        ("7", 1),
    }
    assert {
        "entity": "student",
        "metric": "record_count_by_gender",
        "dimensions": {"gender": None},
        "value": 1,
    } in rows
    assert rows[0]["value"] == 3


@pytest.mark.parametrize(
    ("domain", "entity", "field"),
    [
        ("hr", "employee", "employee_status"),
        ("academic", "student", "student_status"),
        ("admissions", "admission", "admission_status"),
    ],
)
def test_all_three_domains_group_actual_silver_rows(domain, entity, field):
    rows = aggregate(
        domain, entity, pa.Table.from_pylist([{field: "A"}, {field: "A"}, {field: "B"}])
    )
    assert rows[0]["value"] == 3
    assert sum(row["value"] for row in rows if row["metric"] == "record_count_by_" + field) == 3


def test_unknown_file_does_not_become_employee_data():
    rows = aggregate("hr", "file_4", pa.Table.from_pylist([{"employee_status": "ACTIVE"}]))
    assert rows == [{"entity": "file_4", "metric": "record_count", "dimensions": {}, "value": 1}]


def test_empty_table_still_has_zero_total():
    assert aggregate("hr", "employee", pa.table({}))[0]["value"] == 0


def source_fixture(tmp_path, *, mode="production", dataset_id=4, domain="hr", correct_count=True):
    silver, gold = LocalSilverStore(tmp_path / "silver"), LocalSilverStore(tmp_path / "gold")
    job = {
        "job_id": str(uuid4()),
        "silver_snapshot_id": str(uuid4()),
        "scope": "production:test",
        "generation": 1,
        "domain": "hr",
        "dataset_id": 4,
        "recipe_version": RECIPE_VERSION,
        "recipe_fingerprint": RECIPE_FINGERPRINT,
    }
    output = BytesIO()
    pq.write_table(
        pa.Table.from_pylist(
            [
                {"employee_id": "007", "employee_status": "ACTIVE"},
                {"employee_id": "7", "employee_status": "ACTIVE"},
            ]
        ),
        output,
    )
    data = output.getvalue()
    ref = silver.put("silver/production/fixture/data-" + checksum(data) + ".parquet", data)
    manifest = {
        "format": "unilake-silver-v1",
        "snapshot_id": job["silver_snapshot_id"],
        "mode": mode,
        "activation_allowed": mode == "production",
        "scope": job["scope"],
        "entities": {
            "employee": {
                "object": asdict(ref),
                "rows": 2 if correct_count else 3,
                "lineage": [
                    {"key": {"domain_id": domain}, "provenance": {"dataset_id": dataset_id}}
                ],
            }
        },
    }
    encoded = json.dumps(manifest).encode()
    job["silver_manifest_ref"] = json.dumps(
        asdict(
            silver.put("silver/production/fixture/manifest-" + checksum(encoded) + ".json", encoded)
        )
    )
    return job, silver, gold


def test_gold_has_parquet_counts_and_published_lineage(tmp_path):
    job, silver, gold = source_fixture(tmp_path)
    repository = Mock()
    repository.publish.return_value = True
    assert build_gold(job, silver, gold, repository)
    called_job, manifest_ref, result, rows = repository.publish.call_args.args
    assert called_job == job
    assert result["silver_snapshot_id"] == job["silver_snapshot_id"]
    assert result["records_read"] == 2
    assert rows[0]["value"] == 2
    assert result["lineage"]["derivations"][0]["inputs"][0]["version"] == job["silver_snapshot_id"]
    from app.modules.pipeline.repositories.silver_storage import ObjectRef

    assert json.loads(gold.get(ObjectRef(**manifest_ref)))["object"] == result["object"]
    assert pq.read_table(BytesIO(gold.get(ObjectRef(**result["object"])))).num_rows == 2


def test_pending_job_cannot_change_recipe_silently(tmp_path):
    job, silver, gold = source_fixture(tmp_path)
    job["recipe_fingerprint"] = "0" * 64
    repository = Mock()
    with pytest.raises(ValueError, match="GOLD_RECIPE_VERSION_UNSUPPORTED"):
        build_gold(job, silver, gold, repository)
    repository.publish.assert_not_called()


def test_queued_legacy_job_keeps_its_original_recipe(tmp_path):
    from app.modules.pipeline.services.gold_engine import (
        LEGACY_RECIPE_FINGERPRINT,
        LEGACY_RECIPE_VERSION,
    )

    job, silver, gold = source_fixture(tmp_path)
    job.update(recipe_version=LEGACY_RECIPE_VERSION, recipe_fingerprint=LEGACY_RECIPE_FINGERPRINT)
    repository = Mock()
    repository.publish.return_value = True
    assert build_gold(job, silver, gold, repository)
    result = repository.publish.call_args.args[2]
    assert result["recipe_version"] == LEGACY_RECIPE_VERSION
    assert result["recipe_fingerprint"] == LEGACY_RECIPE_FINGERPRINT


def test_business_import_recipe_matches_migration_default():
    from pathlib import Path

    sql = (
        Path(__file__).resolve().parents[1]
        / "infra/postgres/migrations/20261118_business_imports.sql"
    ).read_text()
    assert f"'{RECIPE_VERSION}'" in sql
    assert f"'{RECIPE_FINGERPRINT}'" in sql


def test_changed_silver_manifest_never_publishes_gold(tmp_path):
    job, silver, gold = source_fixture(tmp_path)
    ref = json.loads(job["silver_manifest_ref"])
    (silver.root / ref["key"]).write_bytes(b"changed")
    repository = Mock()
    with pytest.raises(ValueError, match="OBJECT_CHECKSUM_MISMATCH"):
        build_gold(job, silver, gold, repository)
    repository.publish.assert_not_called()


def test_gold_account_never_reads_bronze_or_deletes_files():
    from app.infra.minio.gold_accounts import policy

    statements = policy()["Statement"]
    assert not any("Delete" in action for stmt in statements for action in stmt["Action"])
    resources = [resource for stmt in statements for resource in stmt["Resource"]]
    assert not any(
        "bronze" in resource or "ingestion-uploads" in resource for resource in resources
    )


@pytest.mark.parametrize(
    ("options", "code"),
    [
        ({"mode": "test"}, "GOLD_SILVER_MANIFEST_INVALID"),
        ({"dataset_id": 5}, "GOLD_MIXED_SOURCE_SCOPE_UNSUPPORTED"),
        ({"domain": "academic"}, "GOLD_MIXED_SOURCE_SCOPE_UNSUPPORTED"),
        ({"correct_count": False}, "GOLD_SILVER_ROW_COUNT_MISMATCH"),
    ],
)
def test_invalid_or_mixed_sources_never_publish(tmp_path, options, code):
    job, silver, gold = source_fixture(tmp_path, **options)
    repository = Mock()
    with pytest.raises(ValueError, match=code):
        build_gold(job, silver, gold, repository)
    repository.publish.assert_not_called()


@pytest.mark.parametrize(
    "role", ["HR_STAFF", "ACADEMIC_STAFF", "ADMISSIONS_STAFF", "VIEWER", "HR_MANAGER"]
)
def test_only_administrators_can_trigger_gold(role):
    app.dependency_overrides[get_current_user] = lambda: {"sub": "14", "roles": [role]}
    try:
        client = TestClient(app)
        assert (
            client.post(
                "/api/v1/pipeline/gold/refresh", json={"silver_snapshot_id": str(uuid4())}
            ).status_code
            == 403
        )
        assert client.post(f"/api/v1/pipeline/gold/jobs/{uuid4()}/retry").status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.parametrize(
    ("role", "domains"),
    [
        ("HR_STAFF", ["hr"]),
        ("ACADEMIC_MANAGER", ["academic"]),
        ("ADMISSIONS_STAFF", ["admissions"]),
        ("ADMIN", ["academic", "admissions", "hr"]),
    ],
)
def test_gold_read_scope_comes_from_authenticated_roles(role, domains):
    _, params = access({"roles": [role]}, None)
    assert params["domains"] == domains
    _, params = access({"roles": [role]}, "hr")
    assert params["domains"] == (["hr"] if "hr" in domains else [])
