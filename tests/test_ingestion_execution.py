from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pyarrow.parquet as pq
import pytest
from openpyxl import Workbook

from app.bronze_capture import BronzeCapture
from app.core.exceptions import AppException
from app.modules.datasources.models import DataLayer, Dataset, DataSource, SourceSystem
from app.modules.ingestion.execution import IngestionExecutionService, authorize, validate_context
from app.modules.ingestion.models import IngestionJob
from app.modules.ingestion.readers.contract import ReadRequest, ReadResult
from app.modules.ingestion.schemas import IngestionStart
from app.repositories.ingestion_repo import utcnow


def context():
    return (
        DataSource(
            data_source_id=1,
            source_system_id=1,
            is_active=True,
            source_type="CSV",
            configuration={"delimiter": ",", "encoding": "utf-8", "has_header": True},
            location="input.csv",
        ),
        Dataset(
            dataset_id=2, source_system_id=1, data_layer_id=1, status="ACTIVE", domain="admissions"
        ),
        DataLayer(layer_code="BRONZE"),
        SourceSystem(is_active=True),
    )


def repository():
    repo = AsyncMock()
    repo.context.return_value = context()
    repo.active.return_value = None
    repo.create.return_value = SimpleNamespace(ingestion_job_id=10)
    return repo


@pytest.mark.asyncio
async def test_queue_before_io():
    repo = repository()
    job = await IngestionExecutionService(repo).start(
        IngestionStart(data_source_id=1, dataset_id=2, source_table="input.csv"),
        {"sub": "1", "roles": ["DATA_ADMIN"]},
    )
    assert job.ingestion_job_id == 10
    assert repo.create.call_args.kwargs["status"] == "QUEUED"
    assert repo.context.call_args.kwargs["lock"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("duplicate", [True, False])
async def test_rejection_is_persisted(duplicate):
    repo = repository()
    if duplicate:
        repo.active.return_value = SimpleNamespace(ingestion_job_id=9)
    else:
        repo.context.return_value[0].is_active = False
    with pytest.raises(AppException) as exc:
        await IngestionExecutionService(repo).start(
            IngestionStart(data_source_id=1, dataset_id=2, source_table="input.csv"),
            {"roles": ["ADMIN"]},
        )
    assert exc.value.details == {"ingestion_job_id": 10}
    assert repo.create.call_args.kwargs["status"] == "REJECTED"


@pytest.mark.parametrize("roles", [[], ["VIEWER"], ["ANALYST"], None])
def test_write_authorization(roles):
    with pytest.raises(AppException, match="Không có quyền"):
        authorize({"roles": roles}, write=True)


@pytest.mark.parametrize("table", ["../secret.csv", "a/b.csv", "a\\b.csv", "/input.csv"])
def test_no_source_path_traversal(table):
    with pytest.raises(ValueError, match="INVALID_SOURCE_TABLE"):
        validate_context(context(), table)


@pytest.mark.parametrize(
    ("index", "field", "value", "code"),
    [
        (1, "source_system_id", 99, "DATASET_SOURCE"),
        (1, "domain", "../hr", "INVALID_PARTITION"),
        (2, "layer_code", "SILVER", "BRONZE_DATASET"),
        (0, "source_type", "REST_API", "UNSUPPORTED"),
        (3, "is_active", False, "SOURCE_INACTIVE"),
    ],
)
def test_invalid_catalog(index, field, value, code):
    ctx = context()
    setattr(ctx[index], field, value)
    with pytest.raises(ValueError, match=code):
        validate_context(ctx, "input.csv")


def test_capture_original_bytes(tmp_path):
    path = tmp_path / "input.csv"
    data = b"id,name\r\n001,Example\r\n"
    path.write_bytes(data)
    with BronzeCapture() as capture:
        result = capture.read(
            ReadRequest("CSV", str(path), context()[0].configuration, "input.csv")
        )
        assert result.rows == 1
        assert result.path.read_bytes() == data
        assert result.path != path
    assert not result.path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["read", "write", None])
async def test_execution_stages_and_sanitized_failures(failure):
    repo = repository()
    capture = Mock()
    captured = capture.return_value.__enter__ = Mock()
    capture.return_value.__exit__ = Mock(return_value=False)
    captured.return_value.read.return_value = SimpleNamespace(rows=3)
    store = Mock(return_value={"uri": "s3://bronze/test", "version_id": "v1"})
    if failure == "read":
        captured.return_value.read.side_effect = ValueError("password=do-not-expose")
    elif failure == "write":
        store.side_effect = ValueError("password=do-not-expose")
    await IngestionExecutionService(repo).execute(
        IngestionJob(
            ingestion_job_id=10,
            data_source_id=1,
            dataset_id=2,
            source_table="input.csv",
            created_at=utcnow(),
        ),
        capture,
        store,
    )
    if failure:
        code = "INGESTION_READING_FAILED" if failure == "read" else "INGESTION_WRITING_FAILED"
        repo.finish.assert_awaited_once_with(10, error=code)
    else:
        repo.stage.assert_awaited_once_with(10, "WRITING", 3)
        saved = repo.finish.call_args.kwargs["output"]
        assert saved["uri"] == store.return_value["uri"]
        evidence = saved["lineage"]
        assert evidence["run_ref"] == "ingestion:10"
        assert evidence["derivations"][0]["output"]["version"] == "v1"
        assert evidence["derivations"][0]["inputs"][0]["layer"] == "SOURCE"
        # Writer nhận ID hệ thống từ catalog và thời gian capture, không nhận credentials.
        write_context = store.call_args.args[2]
        assert write_context.source_system_id == 1
        assert write_context.ingestion_job_id == 10
        assert write_context.ingestion_timestamp >= write_context.created_at


def test_capture_excel_original_bytes(tmp_path):
    path = tmp_path / "input.xlsx"
    book = Workbook()
    assert book.active is not None
    book.active.append(["id", "name"])
    book.active.append(["001", "Example"])
    book.save(path)
    book.close()
    with BronzeCapture() as capture:
        result = capture.read(
            ReadRequest(
                "EXCEL",
                str(path),
                {"sheet_name": "Sheet", "header_row": 1, "skip_rows": 0},
                "input.xlsx",
            )
        )
        assert result.rows == 1
        assert result.path.read_bytes() == path.read_bytes()


def test_postgres_export_preserves_typed_values(monkeypatch):
    rows = [(Decimal("123.45"), date(2026, 10, 15), b"bytes", None)]
    reader = Mock()
    reader.read.return_value = ReadResult(["amount", "day", "blob", "empty"], rows, "public.test")
    monkeypatch.setattr("app.bronze_capture.SourceReaderFactory.get_reader", lambda _: reader)
    with BronzeCapture() as capture:
        result = capture.read(ReadRequest("POSTGRESQL", None, {}, "public.test"))
        assert result.rows == 1
        assert pq.read_table(result.path).to_pylist() == [
            {"amount": rows[0][0], "day": rows[0][1], "blob": b"bytes", "empty": None}
        ]


@pytest.mark.asyncio
async def test_queue_unavailable_returns_503():
    from sqlalchemy.exc import SQLAlchemyError

    from app.modules.ingestion.router import start_ingestion

    db = AsyncMock()
    db.scalar.side_effect = SQLAlchemyError("password=private")
    with pytest.raises(AppException) as exc:
        await start_ingestion(
            IngestionStart(data_source_id=1, dataset_id=1, source_table="input.csv"),
            db,
            {"roles": ["ADMIN"]},
        )
    assert exc.value.status_code == 503
    assert exc.value.code == "INGESTION_QUEUE_UNAVAILABLE"
    db.rollback.assert_awaited_once()
