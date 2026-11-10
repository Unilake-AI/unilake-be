"""Kiểm chứng cấu hình nguồn riêng, đọc file thật và công bố Parquet qua luồng Silver."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app.core.config import settings
from app.core.deps import get_current_user
from app.core.exceptions import AppException, app_exception_handler
from app.main import app as application
from app.modules.ingestion.readers.contract import ReadRequest
from app.modules.ingestion.readers.factory import SourceReaderFactory
from app.modules.ingestion.readers.file_snapshot import read_file_snapshot
from app.modules.pipeline import source_cleaning as runner
from app.modules.pipeline.identity_contracts import CrosswalkEntry, CrosswalkSnapshot
from app.modules.pipeline.models import CLEANING_RUNS, PIPELINE_LINEAGE, QUARANTINE_RECORDS
from app.modules.pipeline.router import router
from app.modules.pipeline.silver_publication import SilverPublicationService
from app.modules.pipeline.silver_repository import SILVER_TABLES, SilverRepository
from app.modules.pipeline.silver_storage import LocalSilverStore
from app.modules.pipeline.source_profiles import SourceProfile, load_profile

ROOT = Path(__file__).resolve().parents[1]


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


def definition():
    return {
        "data_source_id": 21,
        "dataset_id": 31,
        "source_system_id": 17,
        "source_type": "CSV",
        "source_table": "positions.csv",
        "domain": "hr",
        "entity": "position",
        "version": "1",
        "scope": "institution",
        "source_schema_version": "1",
        "source_schema": json.loads((ROOT / "docs/data-schema/hr_v1.schema.json").read_text()),
        "contracts": {
            d: json.loads((ROOT / f"docs/standardization/silver-schemas/{d}.json").read_text())
            for d in ("admissions", "academic", "hr")
        },
        "field_mapping": {
            name: name
            for name in ("position_id", "position_code", "position_name", "position_level")
        },
        "crosswalk_release": "source-17-v1",
        "effective_at": "2026-11-01T00:00:00Z",
        "dictionaries": {"position_level": {"Nhân viên": "Staff"}},
        "format_options": {"position_level": {"aliases": [["Nhân viên", "Staff"]]}},
    }


def test_profile_fails_closed_and_fingerprints_changes(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps({"positions": definition()}))
    first = load_profile(str(path), "positions")
    with pytest.raises(ValueError, match="PROFILE_NOT_FOUND"):
        load_profile(str(path), "unknown")
    changed = definition()
    changed["collapse_exact_duplicates"] = True
    assert first.rule_version != SourceProfile.model_validate(changed).rule_version
    changed["source_preview_columns"] = ["private_unmapped_column"]
    with pytest.raises(ValueError, match="SOURCE_PREVIEW_COLUMN_NOT_MAPPED"):
        SourceProfile.model_validate(changed)


@pytest.mark.parametrize("extension", ["csv", "xlsx", "xlsm"])
@pytest.mark.parametrize("invalid", [False, True])
@pytest.mark.parametrize("job_type", ["EXECUTION_V1", "UPLOAD_V1"])
def test_file_to_cleaning_to_production_parquet(
    tmp_path, monkeypatch, extension, invalid, job_type
):
    payload = definition()
    payload["source_table"] = f"positions.{extension}"
    payload["source_type"] = "CSV" if extension == "csv" else "EXCEL"
    profile = SourceProfile.model_validate(payload)
    columns = list(profile.field_mapping)
    rows = [["9", "NV", "Chuyên viên", "Nhân viên"]]
    if invalid:
        rows.append(["10", "", "Thiếu mã chức vụ", "Nhân viên"])
    path = tmp_path / profile.source_table
    if extension == "csv":
        import csv

        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(columns)
            writer.writerows(rows)
        options = {"delimiter": ",", "encoding": "utf-8", "has_header": True}
    else:
        from openpyxl import Workbook

        workbook = Workbook()
        sheet = workbook.active
        assert sheet is not None
        sheet.title = "Positions"
        sheet.append(columns)
        for row in rows:
            sheet.append(row)
        workbook.save(path)
        options = {"sheet_name": "Positions", "header_row": 1, "skip_rows": 0}
    parsed = read_file_snapshot(
        ReadRequest(profile.source_type, str(path), options, path.name),
        SourceReaderFactory.get_reader(profile.source_type),
        source_system_id="17",
        data_source_id="21",
        dataset_id="31",
    )
    uri = f"s3://{settings.minio_bucket_bronze}/hr/2026/11/09/job-42/{path.name}"
    assert parsed.provenance is not None
    parsed = replace(
        parsed, provenance=replace(parsed.provenance, source_ref=uri, object_version_id="pinned")
    )
    assert parsed.provenance is not None
    context = {
        "ingestion_job_id": 42,
        "status": "SUCCESS",
        "job_type": job_type,
        "source_system_id": 17,
        "dataset_source_id": 17,
        "data_source_id": 21,
        "dataset_id": 31,
        "domain": "hr",
        "layer_code": "BRONZE",
        "source_type": profile.source_type,
        "source_table": path.name,
        "rows_read": len(rows),
        "output": {
            "uri": uri,
            "version_id": "pinned",
            "sha256": parsed.provenance.content_sha256,
            "size_bytes": path.stat().st_size,
            "source_type": profile.source_type,
            "parser_options": options,
        },
    }
    runner._validate_job_context(context)
    engine = create_engine(
        f"sqlite:///{tmp_path / 'metadata.db'}",
        execution_options={"schema_translate_map": {"app": None}},
    )
    for table in [*SILVER_TABLES, CLEANING_RUNS, QUARANTINE_RECORDS, PIPELINE_LINEAGE]:
        table.create(engine)
    crosswalk = CrosswalkSnapshot(
        profile.crosswalk_release,
        (
            CrosswalkEntry(
                "17",
                "position",
                ("9",),
                7009,
                "Đối chiếu theo mã nguồn",
                datetime(2020, 1, 1, tzinfo=UTC),
            ),
        ),
    )
    storage = LocalSilverStore(tmp_path / "silver")
    monkeypatch.setattr(runner, "create_engine", lambda *_args, **_kwargs: engine)
    monkeypatch.setattr(runner, "load_profile", lambda *_: profile)
    monkeypatch.setattr(runner, "_job_context", lambda *_: context)
    monkeypatch.setattr(runner, "_identity_service", lambda *_: profile.services(crosswalk))

    def read_pinned(request, *_args, **_kwargs):
        assert request.configuration == options
        assert request.object_version_id == "pinned"
        return parsed

    monkeypatch.setattr(runner, "read_file_snapshot", read_pinned)
    client = MagicMock()
    client.stat_object.return_value = SimpleNamespace(version_id="pinned", size=path.stat().st_size)
    monkeypatch.setattr(runner, "source_reader_client", lambda *_: client)
    monkeypatch.setattr(runner, "silver_writer_client", lambda: None)
    monkeypatch.setattr(runner, "MinioSilverStore", lambda *_: storage)
    result = runner.run_manual_cleaning(
        42, requested_by="1", profile_id="positions", base_snapshot=None
    )
    publisher = SilverPublicationService(
        repository=SilverRepository(engine),
        storage=storage,
        contracts=profile.contracts,
        scope=profile.scope,
    )
    if invalid:
        assert result["status"] == "QUARANTINED"
        assert result["records_quarantined"] == 1
        assert publisher.read_active() == {}
        with engine.begin() as conn:
            assert len(conn.execute(select(QUARANTINE_RECORDS)).all()) == 1
    else:
        assert result["status"] == "PUBLISHED"
        assert result["publication"]["mode"] == "production"
        [record] = publisher.read_active()["position"]
        assert record["position_id"] == 7009
        assert record["position_level"] == "Staff"
        assert record["position_name"] == "Chuyên viên"
        lineage = publisher.read_lineage(result["publication"]["snapshot_id"])
        assert lineage.evidence
        with engine.begin() as conn:
            assert (
                conn.execute(select(CLEANING_RUNS.c.publication_status)).scalar_one() == "PUBLISHED"
            )
    engine.dispose()


def test_profile_cannot_be_applied_to_another_source():
    profile = SourceProfile.model_validate(definition())
    context = profile.model_dump() | {"data_source_id": 99}
    with pytest.raises(ValueError, match="PROFILE_SOURCE_MISMATCH"):
        runner._bind_profile(context, profile)
    context["data_source_id"] = 21
    context["configuration"] = {"delimiter": ";", "password": "không được dùng lại"}
    context["output"] = {}
    with pytest.raises(ValueError, match="PARSER_EVIDENCE_REQUIRED"):
        runner._bind_profile(context, profile)


@pytest.mark.parametrize(("roles", "expected"), [(["DATA_ADMIN"], 200), (["HR_STAFF"], 403)])
def test_api_uses_explicit_profile_and_authorization(monkeypatch, roles, expected):
    called = []

    def run(job_id, **options):
        called.append(options)
        return {
            "run_id": "run",
            "status": "PUBLISHED",
            "ingestion_job_id": job_id,
            "requested_by": options["requested_by"],
            "domain": "hr",
            "entity": "position",
            "input_version": "v1",
            "records_read": 1,
            "records_passed": 1,
            "records_quarantined": 0,
            "records_collapsed": 0,
            "issues": [],
            "publication": {"mode": "production"},
        }

    monkeypatch.setattr("app.modules.pipeline.router.run_manual_cleaning", run)
    app = FastAPI()
    app.add_exception_handler(AppException, app_exception_handler)
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: {"sub": "1", "roles": roles}
    response = TestClient(app).post(
        "/pipeline/cleaning/ingestion-jobs/42", json={"profile_id": "positions"}
    )
    assert response.status_code == expected
    assert bool(called) == (expected == 200)
    if called:
        assert called[0]["profile_id"] == "positions"


def test_application_does_not_register_sample_domain():
    from app.domains.registry import get_domain_registry

    assert not any(item.domain_id == "demo" for item in get_domain_registry().all())
    assert (
        "/api/v1/pipeline/cleaning/ingestion-jobs/{ingestion_job_id}"
        in application.openapi()["paths"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("allowed", [[], ["position_code"]])
async def test_source_preview_requires_explicit_columns(monkeypatch, allowed):
    from fastapi import Response

    from app.modules.pipeline import quarantine_records_router as routes

    calls = []

    def read(record):
        calls.append(record)
        return {"position_code": "NV", "private": "không được trả về"}

    monkeypatch.setattr(routes, "read_quarantine_source", read)
    record = {
        "record_id": "row",
        "domain": "hr",
        "entity": "position",
        "dataset_status": "ACTIVE",
        "dataset_code": "REAL_HR_POSITIONS",
        "quarantine_source": {
            "allowed_columns": allowed,
            "file_name": "positions.csv",
            "source_type": "CSV",
            "version_id": "pinned",
            "sha256": "a" * 64,
        },
        "record_position": 1,
        "locator": "record:1",
        "issues": [{"field": "position_code"}],
    }
    response = Response()
    if not allowed:
        with pytest.raises(AppException) as exc:
            await routes.get_quarantine_source(record, response)
        assert exc.value.status_code == 403
        assert calls == []
    else:
        result = await routes.get_quarantine_source(record, response)
        assert result["data"] == {"position_code": "NV"}
        assert result["invalid_fields"] == ["position_code"]
        assert response.headers["Cache-Control"] == "no-store"


def test_invalid_format_configuration_is_rejected_before_reading_source(tmp_path):
    payload = definition()
    payload["format_options"] = {
        "position_level": {"aliases": [["x", "Staff"], ["x", "Management"]]}
    }
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps({"positions": payload}))
    with pytest.raises(ValueError, match="SILVER_SOURCE_PROFILE_INVALID"):
        load_profile(str(path), "positions")
