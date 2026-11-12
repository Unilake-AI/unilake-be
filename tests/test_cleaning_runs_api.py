from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.deps import get_current_user
from app.core.exceptions import AppException, app_exception_handler
from app.dependencies import get_db
from app.modules.pipeline.repositories.silver_storage import LocalSilverStore, checksum
from app.modules.pipeline.routers import cleaning_runs as api

RUN_ID = "32840586-3d8e-4aeb-9b7e-af4388f4347d"


def test_run_detail_and_report_use_independent_statuses_and_verified_object(monkeypatch, tmp_path):
    storage = LocalSilverStore(tmp_path)
    data = b'{"records_quarantined":2}'
    ref = storage.put(f"report-{checksum(data)}.json", data)
    now = datetime.now(UTC)
    row = {
        "run_id": RUN_ID,
        "ingestion_job_id": 104,
        "dataset_id": 31,
        "domain": "hr",
        "entity": "position",
        "requested_by": "17",
        "session": "test",
        "bronze_version": "pinned",
        "input_version": "input-v1",
        "cleaning_status": "COMPLETED",
        "publication_status": "FAILED",
        "records_read": 100,
        "records_passed": 98,
        "records_quarantined": 2,
        "records_collapsed": 0,
        "records_published": 0,
        "silver_snapshot_id": None,
        "report_ref": asdict(ref),
        "error_code": "SILVER_PUBLICATION_REJECTED",
        "started_at": now,
        "finished_at": now,
    }
    result = MagicMock()
    result.mappings.return_value.one_or_none.return_value = row
    result.mappings.return_value.__iter__.side_effect = lambda: iter([row])
    db = AsyncMock()
    db.execute.return_value = result
    user = {"roles": ["DATA_ADMIN"]}
    app = FastAPI()
    app.add_exception_handler(AppException, app_exception_handler)
    app.include_router(api.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(api, "silver_writer_client", lambda: None)
    monkeypatch.setattr(api, "MinioSilverStore", lambda *_: storage)

    with TestClient(app) as client:
        endpoint = f"/api/v1/cleaning-runs/{RUN_ID}"
        detail = client.get(endpoint).json()
        assert detail["cleaning_status"] == "COMPLETED"
        assert detail["publication_status"] == "FAILED"
        assert detail["records_published"] == 0
        assert detail["report_available"] is True
        assert "report_ref" not in detail

        listing = client.get(
            "/api/v1/cleaning-runs",
            params={"dataset_id": 31, "publication_status": "FAILED", "limit": 1, "offset": 2},
        )
        assert listing.json() == [detail]
        query = db.execute.call_args.args[0].compile()
        assert "cleaning_run.dataset_id =" in str(query)
        assert "cleaning_run.publication_status =" in str(query)
        assert "ORDER BY app.cleaning_run.started_at DESC" in str(query)
        assert list(query.params.values()) == [31, "FAILED", 1, 2]

        assert client.get(endpoint + "/report").json() == {"records_quarantined": 2}
        (tmp_path / ref.key).write_bytes(b"corrupted")
        response = client.get(endpoint + "/report")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "CLEANING_REPORT_UNAVAILABLE"

        row["report_ref"] = None
        response = client.get(endpoint + "/report")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "CLEANING_REPORT_NOT_FOUND"

        user["roles"] = ["VIEWER"]
        assert client.get(endpoint + "/report").status_code == 403
