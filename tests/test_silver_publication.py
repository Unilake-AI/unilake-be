from __future__ import annotations

import csv
import json
import subprocess
import sys
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from openpyxl import Workbook
from sqlalchemy import create_engine, event, select

from app.infra.db.base import Base
from app.modules.ingestion.contracts import FileProvenance, MappingBinding
from app.modules.ingestion.reader_batch import reader_to_batch
from app.modules.ingestion.readers.contract import ReadRequest
from app.modules.ingestion.readers.csv_reader import CSVReader
from app.modules.ingestion.readers.excel_reader import ExcelReader
from app.modules.ingestion.readers.file_snapshot import read_file_snapshot
from app.modules.pipeline.identity_contracts import digest
from app.modules.pipeline.mapping_profiles import build_profile
from app.modules.pipeline.schema_mapping import MappingRegistry, SchemaMappingService
from app.modules.pipeline.silver_pipeline import NormalizationTask, SilverPipeline
from app.modules.pipeline.silver_publication import (
    RULE_IDS,
    BusinessEvidence,
    SilverPublicationService,
)
from app.modules.pipeline.silver_repository import RUNS, SILVER_TABLES, SNAPSHOTS, SilverRepository
from app.modules.pipeline.silver_storage import (
    LocalSilverStore,
    MinioSilverStore,
    SilverError,
    checksum,
)
from app.modules.pipeline.silver_validation import SilverIssue
from tests.test_identity_standardization import CONTRACTS, RECORDS, setup, typed


@pytest.fixture
def environment(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'metadata.db'}",
        execution_options={"schema_translate_map": {"app": None}},
    )

    @event.listens_for(engine, "connect")
    def connect(connection, _):
        connection.isolation_level = None
        connection.execute("PRAGMA foreign_keys=ON")

    @event.listens_for(engine, "begin")
    def begin(connection):
        connection.exec_driver_sql("BEGIN")

    Base.metadata.create_all(engine, tables=SILVER_TABLES)
    repo = SilverRepository(engine)
    store = LocalSilverStore(tmp_path / "objects")
    publisher = SilverPublicationService(
        repository=repo, storage=store, contracts=CONTRACTS, scope="fixture", mode="rehearsal"
    )
    yield publisher, repo, store
    engine.dispose()


def normalized(domain="hr", entity="position", changes=None):
    types, service = setup(domain, entity)
    return service.standardize(typed(types, changes))


def all_entities():
    return tuple(
        normalized(domain, entity)
        for domain, schema in CONTRACTS.items()
        for entity in schema["$defs"]
    )


def runs(repo):
    with repo.engine.begin() as conn:
        return [dict(row) for row in conn.execute(select(RUNS)).mappings()]


def test_all_22_entities_real_parquet_and_audit(environment):
    publisher, repo, store = environment
    receipt = publisher.publish(all_entities())
    assert repo.head(publisher.scope) == receipt.snapshot_id
    tables = publisher.read_active()
    assert len(tables) == 22
    assert tables["program"][0]["tuition_fee_per_credit"] == Decimal("500000.00")
    assert type(tables["program"][0]["tuition_fee_per_credit"]) is Decimal
    assert tables["lecturer"][0]["employee_id"] == tables["employee"][0]["employee_id"]
    for table in tables.values():
        assert table[0]["_run_id"] == receipt.snapshot_id
        assert table[0]["_schema_version"] == "0.1.0-draft"
        assert table[0]["_source_record_id"].startswith("sha256:")
        assert table[0]["_processed_at"].tzinfo is not None
    manifest = json.loads(store.get(receipt.manifest))
    assert manifest["mode"] == "rehearsal"
    assert not manifest["activation_allowed"]
    assert manifest["entities"]["position"]["lineage"][0]["crosswalk_version"] == "fixture-cw-1"
    assert manifest["entities"]["position"]["lineage"][0]["record_locators"] == [
        {"source_record_id": tables["position"][0]["_source_record_id"], "locator": "record:0"}
    ]
    assert len(list(store.root.rglob("*.parquet"))) == 22


def test_retry_with_new_batch_id_reuses_snapshot_without_changing_head(environment):
    publisher, repo, store = environment
    result = normalized()
    first = publisher.publish((result,))
    retry = publisher.publish((replace(result, batch_id="retry-batch"),))
    assert retry.snapshot_id == first.snapshot_id
    assert retry.reused
    assert len(list(store.root.rglob("*.parquet"))) == 1
    second = publisher.publish(
        (normalized(changes={"position_name": "Changed"}),), base_snapshot=first.snapshot_id
    )
    old_retry = publisher.publish((result,))
    assert old_retry.reused
    assert repo.head(publisher.scope) == second.snapshot_id


def test_partial_update_keeps_pinned_parent_tables(environment):
    publisher, _, store = environment
    first = publisher.publish(all_entities())
    second = publisher.publish(
        (normalized(changes={"position_name": "Changed"}),), base_snapshot=first.snapshot_id
    )
    old = publisher.read_snapshot(first.snapshot_id)
    new = publisher.read_snapshot(second.snapshot_id)
    assert new["position"][0]["position_name"] == "Changed"
    assert old["position"][0]["position_name"] != "Changed"
    assert new["employee"] == old["employee"]
    manifest = json.loads(store.get(second.manifest))
    assert manifest["parent_snapshot"] == first.snapshot_id
    assert manifest["parent_manifest"] == json.loads(runs(publisher.repository)[0]["manifest_ref"])


def test_fk_to_missing_parent_blocks_snapshot(environment):
    publisher, repo, _ = environment
    with pytest.raises(SilverError, match="SNAPSHOT_REJECTED"):
        publisher.publish((normalized("admissions", "major"),))
    assert repo.head(publisher.scope) is None
    assert "FK_NOT_IN_SNAPSHOT" in runs(repo)[0]["issues"]


def test_parent_replacement_cannot_orphan_unchanged_children(environment):
    publisher, repo, _ = environment
    first = publisher.publish(all_entities())
    empty = replace(normalized("hr", "department"), accepted=(), total_count=0, inspected_count=0)
    with pytest.raises(SilverError, match="SNAPSHOT_REJECTED"):
        publisher.publish((empty,), base_snapshot=first.snapshot_id)
    assert repo.head(publisher.scope) == first.snapshot_id


@pytest.mark.parametrize("change", ["preview", "rejected", "pii", "float", "duplicate"])
def test_invalid_handoff_never_publishes(environment, change):
    publisher, repo, store = environment
    result = normalized("admissions", "admission_method")
    if change == "preview":
        result = replace(result, preview=True)
    elif change == "rejected":
        result = replace(result, rejected_locators=("bad",))
    elif change == "pii":
        result = replace(
            result,
            accepted=(
                replace(
                    result.accepted[0], values={**result.accepted[0].values, "full_name": "SECRET"}
                ),
            ),
        )
    elif change == "float":
        result = replace(
            result,
            accepted=(
                replace(
                    result.accepted[0], values={**result.accepted[0].values, "max_score": 30.0}
                ),
            ),
        )
    elif change == "duplicate":
        result = replace(
            result,
            accepted=(*result.accepted, replace(result.accepted[0], locator="second")),
            inspected_count=2,
            total_count=2,
        )
    with pytest.raises(SilverError):
        publisher.publish((result,))
    assert repo.head(publisher.scope) is None
    assert not list(store.root.rglob("*.parquet"))
    assert "SECRET" not in runs(repo)[0]["issues"]


def test_unique_code_collision_across_batches(environment):
    publisher, repo, _ = environment
    first = normalized()
    second = replace(
        first,
        batch_id="other",
        accepted=(
            replace(
                first.accepted[0],
                values={**first.accepted[0].values, "position_id": 9999},
                locator="other",
            ),
        ),
    )
    with pytest.raises(SilverError, match="SNAPSHOT_REJECTED"):
        publisher.publish((first, second))
    assert "DUPLICATE_KEY" in runs(repo)[0]["issues"]


def test_metadata_failure_can_resume_staged_artifacts(environment, monkeypatch):
    publisher, repo, store = environment
    original = repo.publish

    def unavailable(_):
        raise RuntimeError("simulated metadata outage")

    monkeypatch.setattr(repo, "publish", unavailable)
    with pytest.raises(SilverError, match="STORAGE_OR_METADATA_FAILURE"):
        publisher.publish((normalized(),))
    attempt = runs(repo)[0]
    assert attempt["status"] == "FAILED"
    assert attempt["manifest_ref"]
    assert repo.head(publisher.scope) is None
    with pytest.raises(SilverError, match="SNAPSHOT_NOT_PUBLISHED"):
        publisher.read_snapshot(attempt["run_id"])
    files_before = sorted(store.root.rglob("*.*"))
    monkeypatch.setattr(repo, "publish", original)
    receipt = publisher.resume(attempt["run_id"])
    assert receipt.snapshot_id == attempt["run_id"]
    assert sorted(store.root.rglob("*.*")) == files_before
    assert publisher.resume(attempt["run_id"]).reused


def test_storage_failure_preserves_old_head_then_retry(environment, monkeypatch):
    publisher, repo, store = environment
    first = publisher.publish((normalized(),))
    original = store.put

    def fail_manifest(key, data):
        if key.endswith(".json"):
            raise OSError("disk full")
        return original(key, data)

    monkeypatch.setattr(store, "put", fail_manifest)
    update = normalized(changes={"position_name": "Updated"})
    with pytest.raises(SilverError, match="STORAGE_OR_METADATA_FAILURE"):
        publisher.publish((update,), base_snapshot=first.snapshot_id)
    assert repo.head(publisher.scope) == first.snapshot_id
    monkeypatch.setattr(store, "put", original)
    final = publisher.publish((update,), base_snapshot=first.snapshot_id)
    assert publisher.read_active()["position"][0]["position_name"] == "Updated"
    with repo.engine.begin() as conn:
        assert len(conn.execute(select(SNAPSHOTS)).all()) == 2
    assert final.snapshot_id != first.snapshot_id


def test_stale_run_cannot_overwrite_new_active_snapshot(environment, monkeypatch):
    publisher, repo, _ = environment
    original = repo.publish
    monkeypatch.setattr(repo, "publish", lambda _: (_ for _ in ()).throw(RuntimeError("pause")))
    with pytest.raises(SilverError):
        publisher.publish((normalized(),))
    staged_id = runs(repo)[0]["run_id"]
    monkeypatch.setattr(repo, "publish", original)
    winner = publisher.publish((normalized(changes={"position_name": "Winner"}),))
    with pytest.raises(SilverError, match="ACTIVE_SNAPSHOT_CHANGED"):
        publisher.resume(staged_id)
    assert repo.head(publisher.scope) == winner.snapshot_id
    with pytest.raises(SilverError, match="SNAPSHOT_NOT_PUBLISHED"):
        publisher.read_snapshot(staged_id)


def test_corrupt_artifact_blocks_reads_and_reuse(environment):
    publisher, _, store = environment
    result = normalized()
    receipt = publisher.publish((result,))
    manifest = json.loads(store.get(receipt.manifest))
    ref = manifest["entities"]["position"]["object"]
    (store.root / ref["key"]).write_bytes(b"broken")
    with pytest.raises(SilverError, match="OBJECT_CHECKSUM_MISMATCH"):
        publisher.read_active()
    with pytest.raises(SilverError, match="OBJECT_CHECKSUM_MISMATCH"):
        publisher.publish((result,))


def test_default_production_is_blocked_with_draft_contract(environment):
    _, repo, store = environment
    publisher = SilverPublicationService(
        repository=repo, storage=store, contracts=CONTRACTS, scope="fixture"
    )
    # Kiểm tra nguồn gốc thực ngay cả trước cổng phê duyệt.
    with pytest.raises(SilverError, match="PROVENANCE_REQUIRED"):
        publisher.publish((normalized(),))
    with pytest.raises(SilverError, match="G1_NOT_APPROVED"):
        publisher._gate({}, {})
    assert repo.head(publisher.scope) is None


def test_production_requires_durable_bronze_version(environment):
    _, repo, store = environment
    publisher = SilverPublicationService(
        repository=repo, storage=store, contracts=CONTRACTS, scope="fixture"
    )
    result = normalized()
    provenance = FileProvenance(
        "SYNTH",
        "1",
        "positions",
        "CSV",
        "s3://bronze/input.csv",
        None,
        "0" * 64,
        "1" * 64,
        "2" * 64,
        "2026-01-01T00:00:00Z",
        "parsed_record_ordinal",
    )
    result = replace(result, provenance=provenance, input_version=provenance.input_version)
    with pytest.raises(SilverError, match="BRONZE_REPLAY_REFERENCE_REQUIRED"):
        publisher.publish((result,))
    assert repo.head(publisher.scope) is None


def test_business_gate_coverage_tracks_source_rule_inventory():
    root = Path(__file__).resolve().parents[1]
    rules = json.loads(
        (root / "docs/standardization/business-rule-review.json").read_text(encoding="utf-8")
    )
    assert {r["id"] for r in rules} == RULE_IDS


@pytest.mark.parametrize("transport", ["CSV", "EXCEL"])
def test_real_reader_through_all_stages_to_parquet(environment, tmp_path, transport):
    publisher, _, _ = environment
    raw = RECORDS["position"][0]
    types, identity = setup()
    if transport == "EXCEL":
        path = tmp_path / "position.xlsx"
        workbook = Workbook()
        assert workbook.active is not None
        workbook.active.append(list(raw))
        workbook.active.append(list(raw.values()))
        workbook.save(path)
        workbook.close()
        reader, config = ExcelReader(), {"sheet_name": "Sheet", "header_row": 1, "skip_rows": 0}
    else:
        path = tmp_path / "position.csv"
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(raw))
            writer.writeheader()
            writer.writerow(raw)
        reader, config = CSVReader(), {"delimiter": ",", "encoding": "utf-8", "has_header": True}
    read = read_file_snapshot(
        ReadRequest(transport, str(path), config),
        reader,
        source_system_id="SYNTH",
        data_source_id="1",
        dataset_id="positions",
    )
    key = types.key
    batch = reader_to_batch(
        read,
        batch_id="reader-batch",
        domain_id="hr",
        source_type=transport,
        binding=MappingBinding(
            key.source_system_id,
            key.entity,
            key.source_schema_version,
            key.mapping_id,
            key.mapping_version,
            key.target_schema_version,
        ),
    )
    profile = build_profile(
        key=key,
        source_type=transport,
        source_columns=batch.source_paths,
        target_fields=tuple(types.spec["properties"]),
        field_paths={n: ("/" + n,) for n in raw},
        deferred_fields=tuple(n for n in types.spec["properties"] if n.startswith("_")),
    )
    receipt = SilverPipeline(publisher).run(
        (NormalizationTask(batch, SchemaMappingService(MappingRegistry((profile,))), identity),)
    )
    output = publisher.read_snapshot(receipt.snapshot_id)["position"][0]
    assert output["position_id"] == 10102
    assert output["_batch_id"] == "reader-batch"
    assert output["_source_system_id"] == "SYNTH"


def test_minio_adapter_pins_version_and_checks_checksum():
    client = MagicMock()
    client.put_object.return_value.version_id = "version-1"
    body = b"parquet-fixture"
    store = MinioSilverStore(client, "silver")
    ref = store.put(f"run/test-{checksum(body)}.parquet", body)
    response = client.get_object.return_value
    response.read.return_value = body
    assert store.get(ref) == body
    client.get_object.assert_called_once_with("silver", ref.key, version_id="version-1")
    response.close.assert_called_once()
    response.release_conn.assert_called_once()
    response.read.return_value = b"wrong"
    with pytest.raises(SilverError, match="OBJECT_CHECKSUM_MISMATCH"):
        store.get(ref)


def test_local_storage_rejects_path_escape(environment):
    _, _, store = environment
    with pytest.raises(SilverError, match="INVALID_OBJECT_KEY"):
        store.put("../outside", b"x")


def test_self_reference_cycle_is_rejected(environment):
    publisher, repo, _ = environment
    department = normalized("hr", "department")
    row = department.accepted[0]
    department = replace(
        department,
        accepted=(
            replace(
                row, values={**row.values, "parent_department_id": row.values["department_id"]}
            ),
        ),
    )
    with pytest.raises(SilverError, match="SNAPSHOT_REJECTED"):
        publisher.publish((department,))
    assert "REFERENCE_CYCLE" in runs(repo)[0]["issues"]


def test_empty_entity_has_typed_parquet(environment):
    publisher, _, _ = environment
    empty = replace(normalized(), accepted=(), inspected_count=0, total_count=0)
    receipt = publisher.publish((empty,))
    assert publisher.read_snapshot(receipt.snapshot_id) == {"position": []}


def test_failed_upstream_issue_preserves_locator_and_code(environment):
    publisher, repo, _ = environment
    rejected = normalized(changes={"position_level": "SECRET_UNKNOWN"})
    with pytest.raises(SilverError, match="SNAPSHOT_REJECTED"):
        publisher.publish((rejected,))
    issues = json.loads(runs(repo)[0]["issues"])
    assert any(i["locator"] == "record:0" and i["code"] == "UNKNOWN_REFERENCE_CODE" for i in issues)
    assert "SECRET_UNKNOWN" not in repr(issues)


@pytest.mark.parametrize("failure", ["missing_gate", "coverage", "dq_error", "valid"])
def test_production_business_gate_contract_without_claiming_real_approval(environment, failure):
    _, repo, store = environment
    # Chỉ sao chép chính sách giả lập: không thay đổi hợp đồng/phê duyệt đã lưu của dự án.
    contracts = deepcopy(CONTRACTS)
    for contract in contracts.values():
        contract["x-version"] = "fixture-approved"
        contract["x-approval"] = "APPROVED"
        for spec in contract["$defs"].values():
            spec["properties"]["_schema_version"]["const"] = "fixture-approved"

    class FixtureGate:
        version = "fixture-dq-1"

        def evaluate(self, tables, manifest):
            tables.clear()  # Phần mở rộng bộ kiểm tra không được sửa đầu vào của bộ công bố.
            if failure == "coverage":
                return BusinessEvidence(frozenset())
            if failure == "dq_error":
                return BusinessEvidence(RULE_IDS, (SilverIssue("*", "*", "fixture", "DQ_FAILED"),))
            return BusinessEvidence(RULE_IDS)

    policy = {
        "activation_allowed": True,
        "g1_evidence": "TEST_ONLY",
        "schema_fingerprint": digest(contracts),
        "dq_version": "fixture-dq-1",
        "dq_rule_ids": sorted(RULE_IDS),
    }
    publisher = SilverPublicationService(
        repository=repo,
        storage=store,
        contracts=contracts,
        scope="fixture-gate",
        approval=policy,
        business_gate=None if failure == "missing_gate" else FixtureGate(),
    )
    tables = {"position": []}
    if failure == "valid":
        publisher._gate(tables, {})
        assert tables == {"position": []}
    else:
        with pytest.raises(SilverError, match="BUSINESS_GATE"):
            publisher._gate(tables, {})
    assert repo.head(publisher.scope) is None


def test_rehearsal_cli_writes_parquet_and_is_idempotent(tmp_path):
    root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        str(root / "scripts/standardization/rehearse_p5.py"),
        "--output",
        str(tmp_path / "cli"),
    ]
    first = json.loads(subprocess.run(command, capture_output=True, text=True, check=True).stdout)
    second = json.loads(subprocess.run(command, capture_output=True, text=True, check=True).stdout)
    assert first["snapshot_id"] == second["snapshot_id"]
    assert second["reused"] is True
    assert second["row_counts"] == {"admission_method": 1, "semester": 1, "position": 1}
    assert second["production_activated"] is False
