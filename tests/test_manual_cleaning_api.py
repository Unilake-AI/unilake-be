from __future__ import annotations

import json
from dataclasses import replace
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.deps import get_current_user
from app.core.exceptions import AppException, app_exception_handler
from app.modules.ingestion.contracts import BronzeBatch, FileProvenance, MappingBinding
from app.modules.ingestion.readers.file_snapshot import fingerprint
from app.modules.pipeline.cleaning_pipeline import CleaningPipeline, CleaningTask
from app.modules.pipeline.duplicate_records import build_duplicate_profile
from app.modules.pipeline.format_validation import build_format_profile
from app.modules.pipeline.manual_cleaning import (
    PROFILE_VERSION,
    _case,
    _format_options,
    _read_json,
    _validate_job_context,
)
from app.modules.pipeline.mapping_profiles import pointer
from app.modules.pipeline.missing_values import MissingValueProfile, MissingValueRule
from app.modules.pipeline.router import router
from app.modules.pipeline.silver_storage import LocalSilverStore


def _demo_cleaning_task(domain: str) -> CleaningTask:
    case = _case(domain)
    source_row = case["records"][0]
    source_columns = tuple(source_row)
    contract = _read_json(f"docs/standardization/silver-schemas/{domain}.json")
    schema = _read_json(f"docs/data-schema/{domain}_v1.schema.json")
    target_to_source = {
        target: paths[0].removeprefix("/") for target, paths in case["field_paths"].items()
    }
    key = MappingBinding(
        source_system_id="17",
        entity=case["entity"],
        source_schema_version="1",
        mapping_id=f"manual-{domain}-csv",
        mapping_version="1",
        target_schema_version=contract["x-version"],
    )
    provenance = FileProvenance(
        source_system_id="17",
        data_source_id="21",
        dataset_id="31",
        source_type="CSV",
        source_ref=f"s3://{settings.minio_bucket_bronze}/{domain}/2026/10/19/job-42/input.csv",
        object_version_id="bronze-version",
        content_sha256="a" * 64,
        parser_fingerprint="demo-parser",
        columns_fingerprint=fingerprint(list(source_columns)),
        captured_at="2026-10-19T00:00:00+00:00",
        locator_kind="parsed_record_ordinal",
    )
    batch = BronzeBatch(
        batch_id="job-42",
        domain_id=domain,
        source_type="CSV",
        records=(source_row,),
        metadata={"input_version": provenance.input_version},
        mapping_binding=MappingBinding(
            key.source_system_id,
            key.entity,
            key.source_schema_version,
            key.mapping_id,
            key.mapping_version,
            key.target_schema_version,
        ),
        record_locators=("record:1",),
        source_paths=tuple(pointer(name) for name in source_columns),
        provenance=provenance,
    )
    entity_schema = schema["entities"][case["entity"]]
    rules = tuple(
        MissingValueRule(
            target_to_source[column["name"]],
            nullable=column["nullable"],
        )
        for column in entity_schema["columns"]
        if column["name"] in target_to_source
    )
    return CleaningTask(
        batch,
        MissingValueProfile(
            domain,
            case["entity"],
            PROFILE_VERSION,
            tuple(target_to_source.values()),
            rules,
        ),
        build_format_profile(
            schema,
            domain=domain,
            entity=case["entity"],
            version=PROFILE_VERSION,
            target_to_source=target_to_source,
            source_options=_format_options(domain, case),
        ),
        build_duplicate_profile(
            schema,
            domain=domain,
            entity=case["entity"],
            version=PROFILE_VERSION,
            target_to_source=target_to_source,
        ),
    )


@pytest.mark.parametrize(
    ("domain", "expected"),
    [
        ("admissions", {"diem": "30.00", "active": "true"}),
        ("academic", {"tu": "2026-09-01", "den": "2027-01-15", "state": "Ongoing"}),
        ("hr", {"cap": "Staff"}),
    ],
)
def test_demo_profiles_clean_source_formats_before_standardization(
    domain: str, expected: dict[str, object]
) -> None:
    result = CleaningPipeline().clean(_demo_cleaning_task(domain))

    assert result.report.records_passed == 1
    assert result.report.records_quarantined == 0
    assert result.report.records_collapsed == 0
    assert all(issue.severity == "INFO" for issue in result.report.format_issues)
    assert result.batch.records[0] == {
        **_case(domain)["records"][0],
        **expected,
    }


def _valid_job_context() -> dict[str, Any]:
    return {
        "status": "SUCCESS",
        "job_type": "EXECUTION_V1",
        "domain": "admissions",
        "source_code": "DEMO_FLOW_admissions",
        "dataset_code": "DEMO_FLOW_admissions_BRONZE",
        "dataset_source_id": 17,
        "source_system_id": 17,
        "layer_code": "BRONZE",
        "configuration": {"has_header": True},
        "location": "/data/standardization/live-demo",
        "source_type": "CSV",
        "source_table": "01-admissions.csv",
        "output": {
            "uri": (
                f"s3://{settings.minio_bucket_bronze}/admissions/2026/10/19/"
                "job-42/01-admissions.csv"
            ),
            "version_id": "bronze-version",
            "sha256": "a" * 64,
            "size_bytes": 100,
        },
        "ingestion_job_id": 42,
        "dataset_id": 31,
        "rows_read": 1,
    }


def test_manual_runner_accepts_only_successful_pinned_demo_bronze_jobs() -> None:
    context = _valid_job_context()
    _validate_job_context(context)

    invalid = {**context, "source_code": "UNREGISTERED_SOURCE"}
    with pytest.raises(ValueError, match="DEDICATED_DEMO_CATALOG_REQUIRED"):
        _validate_job_context(invalid)


@pytest.mark.parametrize(
    ("domain", "rows_read", "status"),
    [
        ("hr", 10, "PUBLISHED_WITH_QUARANTINE"),
        ("hr", 9, "QUARANTINED"),
        ("academic", 10, "QUARANTINED"),
    ],
)
def test_manual_runner_applies_partial_policy_and_persists_report(
    monkeypatch, tmp_path, domain, rows_read, status
) -> None:
    from app.modules.pipeline import manual_cleaning as runner

    task = _demo_cleaning_task(domain)
    sample = task.batch.records[0]
    valid = [
        {
            **sample,
            "id": f"{100 + i}",
            "ma": f"P{chr(65 + i)}" if domain == "hr" else f"{2026 + i}-1",
        }
        for i in range(rows_read - 1)
    ]
    task = replace(
        task,
        batch=replace(
            task.batch,
            records=(*valid, {**sample, "id": ""}),
            record_locators=tuple(f"record:{i}" for i in range(rows_read)),
        ),
    )
    storage = LocalSilverStore(tmp_path)
    monkeypatch.setattr(runner, "create_engine", lambda *_, **__: MagicMock())
    monkeypatch.setattr(runner, "_job_context", lambda *_: _valid_job_context())
    monkeypatch.setattr(
        runner, "_read_bronze", lambda _: (task, None, None, None, runner._case(domain))
    )
    monkeypatch.setattr(runner, "silver_writer_client", lambda: None)
    monkeypatch.setattr(runner, "MinioSilverStore", lambda *_: storage)
    monkeypatch.setattr(runner, "_identity_service", lambda *_: None)
    monkeypatch.setattr(runner, "SilverPublicationService", lambda **_: MagicMock())
    pipeline = MagicMock()
    monkeypatch.setattr(runner, "SilverPipeline", lambda _: pipeline)

    result = runner.run_manual_cleaning(42, requested_by="17", base_snapshot=None)
    assert result["status"] == status
    assert result["records_quarantined"] == 1
    ref = result["quarantine_report"]
    saved = json.loads((tmp_path / ref["key"]).read_bytes())
    assert saved["bronze"]["version_id"] == "bronze-version"
    assert any(issue["locator"] == f"record:{rows_read - 1}" for issue in saved["issues"])
    if status == "PUBLISHED_WITH_QUARANTINE":
        assert result["publication"]["records_published"] == rows_read - 1
        assert len(pipeline.run.call_args.args[0][0].batch.records) == rows_read - 1
        assert pipeline.run.call_args.kwargs["quarantine_report"].sha256 == ref["sha256"]
    else:
        pipeline.run.assert_not_called()


def test_manual_cleaning_api_requires_admin_and_returns_pipeline_report(monkeypatch) -> None:
    from app.modules.pipeline import router as pipeline_router

    captured = {}

    def run(job_id: int, **kwargs):
        captured.update(job_id=job_id, **kwargs)
        return {
            "status": "PUBLISHED",
            "run_id": "32840586-3d8e-4aeb-9b7e-af4388f4347d",
            "ingestion_job_id": job_id,
            "requested_by": kwargs["requested_by"],
            "domain": "hr",
            "entity": "position",
            "input_version": "sha256:" + "a" * 64,
            "records_read": 1,
            "records_passed": 1,
            "records_quarantined": 0,
            "records_collapsed": 0,
            "issues": [],
            "publication": {"snapshot_id": "snapshot-1", "mode": "rehearsal"},
        }

    monkeypatch.setattr(pipeline_router, "run_manual_cleaning", run)
    app = FastAPI()
    app.add_exception_handler(AppException, app_exception_handler)
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: {
        "sub": "17",
        "roles": ["DATA_ADMIN"],
    }

    response = TestClient(app).post(
        "/pipeline/cleaning/ingestion-jobs/42",
        json={"session": "manual-1", "base_snapshot": None},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "PUBLISHED"
    assert response.json()["publication"]["mode"] == "rehearsal"
    assert captured == {
        "job_id": 42,
        "requested_by": "17",
        "session": "manual-1",
        "base_snapshot": None,
    }


def test_manual_cleaning_api_rejects_non_admin(monkeypatch) -> None:
    from app.modules.pipeline import router as pipeline_router

    def should_not_run(*_args: object, **_kwargs: object) -> None:
        pytest.fail(f"Cleaning runner must not run for a non-admin: args={_args}, kwargs={_kwargs}")

    monkeypatch.setattr(pipeline_router, "run_manual_cleaning", should_not_run)
    app = FastAPI()
    app.add_exception_handler(AppException, app_exception_handler)
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: {
        "sub": "18",
        "roles": ["HR_STAFF"],
    }

    response = TestClient(app).post(
        "/pipeline/cleaning/ingestion-jobs/42",
        json={"session": "manual-1"},
    )

    assert response.status_code == 403
