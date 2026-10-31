"""Kiểm chứng các tệp demo thủ công đã bàn giao bằng reader và luồng công bố thật."""

from __future__ import annotations

import hashlib
import importlib.util
import os
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine

from app.infra.db.base import Base
from app.modules.pipeline.identity_contracts import CrosswalkEntry, CrosswalkSnapshot
from app.modules.pipeline.silver_repository import SILVER_TABLES, SilverRepository
from app.modules.pipeline.silver_storage import LocalSilverStore, SilverError

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "live_demo", ROOT / "scripts/standardization/live_demo.py"
)
assert spec is not None
assert spec.loader is not None
demo = importlib.util.module_from_spec(spec)
cwd = Path.cwd()
try:
    spec.loader.exec_module(demo)
finally:
    os.chdir(cwd)


@pytest.mark.parametrize("extension", ["csv", "xlsx"])
def test_delivered_files_publish_reject_retry_and_update(tmp_path, extension):
    entries = tuple(
        CrosswalkEntry(
            "DEMO_live_" + c["domain"],
            c["entity"],
            (c["records"][0]["id"],),
            100 + i,
            "synthetic",
            demo.WHEN,
        )
        for i, c in enumerate(demo.profiles())
    )
    snapshot = CrosswalkSnapshot(demo.RELEASE, entries)
    engine = create_engine("sqlite://", execution_options={"schema_translate_map": {"app": None}})
    Base.metadata.create_all(engine, tables=SILVER_TABLES)
    publisher = demo.SilverPublicationService(
        repository=SilverRepository(engine),
        storage=LocalSilverStore(tmp_path),
        contracts={
            d: demo.read_json(f"docs/standardization/silver-schemas/{d}.json") for d in demo.DOMAINS
        },
        scope="demo-file-verification",
        mode="rehearsal",
    )
    cases = [
        ("01-admissions", "admissions", None),
        ("02-academic", "academic", None),
        ("03-hr", "hr", None),
        ("04-bad-date", "academic", "DATE_PARSE_ERROR"),
        ("05-unknown-id", "hr", "SOURCE_ID_UNMAPPED"),
        ("06-hr-updated", "hr", None),
    ]
    tables = {}
    for name, domain, error in cases:
        result = demo.normalize(
            domain, str(ROOT / f"data/standardization/live-demo/{name}.{extension}"), snapshot
        )
        before = publisher.repository.head(publisher.scope)
        if error:
            assert error in [i.code for i in (*result.type_issues, *result.issues)]
            with pytest.raises(SilverError):
                publisher.publish((result,), base_snapshot=before)
            assert publisher.repository.head(publisher.scope) == before
        else:
            assert len(result.accepted) == 1
            receipt = publisher.publish((result,), base_snapshot=before)
            tables = publisher.read_snapshot(receipt.snapshot_id)
            assert len(tables[result.key.entity]) == 1
            retry = publisher.publish((result,), base_snapshot=before)
            assert retry.reused
            assert retry.snapshot_id == receipt.snapshot_id
    assert set(tables) == {"admission_method", "semester", "position"}
    assert tables["position"][0]["position_name"] == "Giảng viên giả lập cập nhật"
    engine.dispose()


def job_row():
    return {
        "ingestion_job_id": 12,
        "status": "SUCCESS",
        "job_type": "EXECUTION_V1",
        "source_code": "DEMO_FLOW_hr",
        "domain": "hr",
        "source_system_id": 42,
        "dataset_source_id": 42,
        "dataset_code": "DEMO_FLOW_hr_BRONZE",
        "layer_code": "BRONZE",
        "source_table": "03-hr.xlsx",
        "rows_read": 1,
        "output": {
            "uri": "s3://bronze/hr/2026/10/16/job-12/03-hr.xlsx",
            "version_id": "pinned-v1",
            "sha256": "a" * 64,
            "size_bytes": 123,
        },
    }


def test_job_adapter_uses_published_key():
    result = demo.validate_job_input(job_row(), "bronze")
    assert result["object"] == "hr/2026/10/16/job-12/03-hr.xlsx"
    assert result["output"]["version_id"] == "pinned-v1"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "RUNNING"),
        ("job_type", "OTHER"),
        ("source_code", "REAL_HR"),
        ("dataset_code", "OTHER"),
        ("dataset_source_id", 99),
        ("layer_code", "SILVER"),
        ("source_table", "different.xlsx"),
    ],
)
def test_job_adapter_rejects_unpublished_or_unrelated_job(field, value):
    row = job_row()
    row[field] = value
    with pytest.raises(ValueError, match=r"SUCCESSFUL_BRONZE|DEDICATED_DEMO|BRONZE_JOB_OUTPUT"):
        demo.validate_job_input(row, "bronze")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("uri", "s3://other/hr/2026/10/16/job-12/03-hr.xlsx"),
        ("uri", "s3://bronze/hr/2026/10/16/job-13/03-hr.xlsx"),
        ("uri", "s3://bronze/hr/2026/10/16/job-12/snapshot.parquet"),
        ("version_id", None),
        ("sha256", ""),
        ("size_bytes", None),
    ],
)
def test_job_adapter_rejects_invalid_evidence(field, value):
    row = deepcopy(job_row())
    row["output"][field] = value
    with pytest.raises(ValueError, match=r"BRONZE_JOB_OUTPUT|BRONZE_JOB_EVIDENCE"):
        demo.validate_job_input(row, "bronze")


@pytest.mark.parametrize(
    ("checksum", "version", "rows"),
    [("b" * 64, "pinned-v1", 1), ("a" * 64, "latest-v2", 1), ("a" * 64, "pinned-v1", 2)],
)
def test_job_capture_rejects_changed_bytes_version_or_rows(checksum, version, rows):
    raw = SimpleNamespace(
        rows_read=rows,
        provenance=SimpleNamespace(content_sha256=checksum, object_version_id=version),
    )
    with pytest.raises(ValueError, match="BRONZE_JOB_CAPTURE_MISMATCH"):
        demo.verify_job_capture(raw, job_row())


def test_job_handoff_preserves_catalog_ids_and_pinned_bronze_version(monkeypatch):
    from app.modules.ingestion.readers import file_snapshot

    path = ROOT / "data/standardization/live-demo/03-hr.xlsx"
    row = job_row()
    row.update(data_source_id=61, dataset_id=71)
    row["output"].update(
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(), size_bytes=path.stat().st_size
    )

    @contextmanager
    def resolve(request):
        assert request.object_version_id == "pinned-v1"
        yield replace(request, location=str(path))

    monkeypatch.setattr(file_snapshot, "resolve_file_request", resolve)
    snapshot = CrosswalkSnapshot(
        demo.JOB_RELEASE,
        (
            CrosswalkEntry(
                "42", "position", ("009",), 7009, "Synthetic ingestion demo v1", demo.WHEN
            ),
        ),
    )
    result = demo.normalize(
        "hr", row["output"]["uri"], snapshot, version="pinned-v1", job_context=row
    )
    assert result.accepted[0].values["position_id"] == 7009
    assert result.provenance.source_system_id == "42"
    assert result.provenance.data_source_id == "61"
    assert result.provenance.dataset_id == "71"
    assert result.provenance.object_version_id == "pinned-v1"
    assert result.provenance.source_ref == row["output"]["uri"]
