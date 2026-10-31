from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from openpyxl import Workbook

from app.bronze_capture import BronzeCapture
from app.core.exceptions import AppException
from app.modules.ingestion.execution import IngestionExecutionService
from app.modules.ingestion.input_validator import ValidationBlockedError, required_rule_ids
from app.modules.ingestion.models import IngestionJob
from app.modules.ingestion.readers.contract import ReadRequest
from app.modules.ingestion.schemas import IngestionStart
from app.modules.ingestion.validation import (
    CSVOptions,
    ExcelOptions,
    PostgreSQLOptions,
    ResourceLimits,
    SourceColumn,
    ValidationPolicy,
)
from app.modules.ingestion.validation_binding import bind_policy, pin_policy
from tests.test_ingestion_execution import context, repository


def policy(**overrides):
    values = {
        "policy_id": "demo",
        "version": "1",
        "source_system_id": 1,
        "data_source_id": 1,
        "dataset_id": 2,
        "parser": CSVOptions(delimiter=",", encoding="utf-8", has_header=True),
        "columns": (SourceColumn(name="id"), SourceColumn(name="name")),
        "limits": ResourceLimits(
            max_file_bytes=1_000_000,
            max_columns=10,
            max_records=100,
            max_cell_chars=10_000,
            timeout_seconds=30,
            memory_limit_bytes=10_000_000,
        ),
    }
    values.update(overrides)
    return ValidationPolicy(**values)


def request(path, selected=None):
    selected = selected or policy()
    return ReadRequest(
        selected.parser.kind,
        str(path),
        {},
        path.name,
        validation_policy=selected,
        ingestion_job_id=10,
    )


def test_csv_multiline_preserves_bytes_and_all_required_checks(tmp_path):
    path = tmp_path / "input.csv"
    raw = 'id,name\r\n001,"Nguyễn\nVăn A"\r\n'.encode()
    path.write_bytes(raw)
    with BronzeCapture() as capture:
        artifact = capture.read(request(path))
        assert artifact.path.read_bytes() == raw
        assert artifact.rows == 1
        report = artifact.validation_report
        assert report.result == "PASS"
        assert {c.rule_id for c in report.checks} == set(required_rule_ids("CSV"))
        assert all(c.status == "PASS" for c in report.checks)


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (b"id,id\n1,2\n", "HEADER_DUPLICATE"),
        (b"id,\n1,2\n", "HEADER_EMPTY"),
        (b"id,other\n1,2\n", "SOURCE_SCHEMA_MISMATCH"),
        (b"id,name\n1\n", "SOURCE_SCHEMA_MISMATCH"),
        (b"id,name\n1,2,3\n", "SOURCE_SCHEMA_MISMATCH"),
        (b"id,name\n\n", "SOURCE_SCHEMA_MISMATCH"),
        (b'id,name\n1,"bad\n', "FILE_PARSE_ERROR"),
        (b"id,name\n1,\xff\n", "ENCODING_ERROR"),
        (b"id,name\n", "EMPTY_SOURCE"),
        (b"", "HEADER_EMPTY"),
    ],
)
def test_bad_csv_is_blocked_without_leaking_values(tmp_path, raw, code):
    path = tmp_path / "input.csv"
    path.write_bytes(raw)
    with BronzeCapture() as capture, pytest.raises(ValidationBlockedError) as exc:
        capture.read(request(path))
    report = exc.value.report
    assert not report.can_accept
    assert report.issues[-1].code == code
    assert "raw_value" not in report.model_dump_json()


def test_csv_extra_and_empty_warnings(tmp_path):
    path = tmp_path / "input.csv"
    path.write_text("id,name,extra\n", encoding="utf-8")
    with BronzeCapture() as capture:
        result = capture.read(
            request(path, policy(allow_extra_columns=True, allow_empty_records=True))
        )
        assert result.validation_report.result == "WARNING"
        assert result.validation_report.total_warnings == 2
        assert result.rows == 0


def test_headerless_csv_uses_positional_contract(tmp_path):
    path = tmp_path / "input.csv"
    path.write_text("001,Name\n", encoding="utf-8")
    selected = policy(
        parser=CSVOptions(
            delimiter=",", encoding="utf-8", has_header=False, positional_columns=("id", "name")
        )
    )
    with BronzeCapture() as capture:
        assert capture.read(request(path, selected)).rows == 1


@pytest.mark.parametrize(
    ("limit", "value"),
    [("max_records", 1), ("max_file_bytes", 4), ("max_cell_chars", 2), ("memory_limit_bytes", 1)],
)
def test_scan_limits_block_input(tmp_path, limit, value):
    path = tmp_path / "input.csv"
    path.write_text("id,name\n1,Name\n2,Name\n", encoding="utf-8")
    limits = policy().limits.model_dump()
    limits[limit] = value
    with BronzeCapture() as capture, pytest.raises(ValidationBlockedError) as exc:
        capture.read(request(path, policy(limits=ResourceLimits(**limits))))
    assert exc.value.report.issues[-1].code == "RESOURCE_LIMIT_EXCEEDED"
    assert not exc.value.report.scan_complete


def excel_file(path, rows):
    book = Workbook()
    for row in rows:
        book.active.append(row)
    book.save(path)
    book.close()


@pytest.mark.parametrize(
    ("rows", "code"),
    [
        ([["id", "name"], [1, "=1+1"]], "EXCEL_FORMULA_UNSUPPORTED"),
        ([["id", "name"], [1, "#DIV/0!"]], "EXCEL_CELL_ERROR"),
        ([["id", " id "]], "HEADER_DUPLICATE"),
        ([["id", "name"], [1, "Name", "extra"]], "EXCEL_UNNAMED_COLUMN"),
    ],
)
def test_excel_rejection(tmp_path, rows, code):
    path = tmp_path / "input.xlsx"
    excel_file(path, rows)
    selected = policy(parser=ExcelOptions(sheet_name="Sheet"))
    with BronzeCapture() as capture, pytest.raises(ValidationBlockedError) as exc:
        capture.read(request(path, selected))
    assert exc.value.report.issues[-1].code == code
    assert exc.value.report.issues[-1].row_number is not None


def test_excel_counts_blank_rows_preserves_bytes_and_dates(tmp_path):
    path = tmp_path / "input.xlsx"
    excel_file(path, [["id", "name"], [None, None], [1, date(2026, 10, 22)]])
    with BronzeCapture() as capture:
        artifact = capture.read(request(path, policy(parser=ExcelOptions(sheet_name="Sheet"))))
        assert artifact.path.read_bytes() == path.read_bytes()
        assert artifact.validation_report.blank_rows_skipped == 1
        assert artifact.validation_report.result == "PASS"
        assert artifact.rows == 1


def mock_postgres(monkeypatch, rows):
    conn = MagicMock()
    conn.__enter__.return_value = conn
    columns = [
        SimpleNamespace(name="id", type_code=23, precision=None, scale=None),
        SimpleNamespace(name="amount", type_code=1700, precision=10, scale=2),
    ]
    conn.execute.side_effect = [
        None,
        SimpleNamespace(description=columns),
        SimpleNamespace(fetchall=lambda: [(23, "int4"), (1700, "numeric")]),
    ]
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.__iter__.return_value = iter(rows)
    monkeypatch.setattr("app.modules.ingestion.input_validator.psycopg.connect", lambda **_: conn)
    selected = policy(
        parser=PostgreSQLOptions(
            schema_name="public", table_name="demo", connection_config_version="v1"
        ),
        columns=(
            SourceColumn(name="id", allowed_source_types=("int4",)),
            SourceColumn(name="amount", allowed_source_types=("numeric",)),
        ),
        allow_empty_records=True,
    )
    req = ReadRequest(
        "POSTGRESQL",
        None,
        {
            "host": "host",
            "port": 5432,
            "database": "db",
            "username": "reader",
            "password": "secret",
        },
        "public.demo",
        validation_policy=selected,
        ingestion_job_id=10,
    )
    return conn, req


@pytest.mark.parametrize("rows", [[], [(1, Decimal("123.45"))], [(1, None)]])
def test_pg_snapshot_preserves_types_even_for_empty_and_null(monkeypatch, rows):
    conn, req = mock_postgres(monkeypatch, rows)
    with BronzeCapture() as capture:
        result = capture.read(req)
        table = pq.read_table(result.path)
        assert table.schema.field("id").type == pa.int32()
        assert table.schema.field("amount").type == pa.decimal128(10, 2)
        assert table.num_rows == len(rows)
        assert result.validation_report.can_accept
        assert "secret" not in result.validation_report.model_dump_json()
    assert conn.execute.call_args_list[0].args[0].endswith("REPEATABLE READ READ ONLY")
    conn.cursor.assert_called_once_with(name="bronze_validation")


def test_policy_binding_and_snapshot_survive_catalog_parser_edit():
    ctx = context()
    req = ReadRequest("CSV", "input.csv", ctx[0].configuration, "input.csv")
    snapshot = pin_policy(policy().model_dump(mode="json"), ctx, req)
    req.configuration["delimiter"] = ";"
    ctx[0].location = "new.csv"
    bound = bind_policy(snapshot, ctx, req, 10)
    assert bound.validation_policy.parser.delimiter == ","
    assert bound.location == "input.csv"
    with pytest.raises(ValueError, match="BINDING_MISMATCH"):
        pin_policy(policy(dataset_id=99).model_dump(mode="json"), ctx, req)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "validation", "report", "write"])
async def test_worker_persists_before_upload_and_blocks_on_failure(tmp_path, monkeypatch, failure):
    # Không cần thread cho test thứ tự gọi; reader/capture vẫn đọc file thật.
    async def inline(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr("app.modules.ingestion.execution.run_io", inline)
    path = tmp_path / "input.csv"
    path.write_text(
        "id,name\n1,Name\n" if failure != "validation" else "id,id\n1,2\n", encoding="utf-8"
    )
    repo = repository()
    ctx = repo.context.return_value
    ctx[0].location = str(path)
    snapshot = pin_policy(
        policy().model_dump(mode="json"), ctx, ReadRequest("CSV", str(path), {}, "input.csv")
    )
    events = []

    async def save(*_):
        events.append("report")
        if failure == "report":
            raise RuntimeError("db unavailable")

    repo.save_validation.side_effect = save

    def store(*_):
        events.append("upload")
        if failure == "write":
            raise RuntimeError("storage unavailable")
        return {"uri": "s3://bronze/test"}

    await IngestionExecutionService(repo).execute(
        IngestionJob(
            ingestion_job_id=10,
            data_source_id=1,
            dataset_id=2,
            source_table="input.csv",
            created_at=datetime(2026, 10, 22),
            validation_input=snapshot,
        ),
        BronzeCapture,
        store,
    )
    assert events == (["report", "upload"] if failure in {None, "write"} else ["report"])
    report = repo.save_validation.call_args.args[1]
    assert report.can_accept is (failure != "validation")
    if failure:
        assert "error" in repo.finish.call_args.kwargs
    else:
        assert repo.finish.call_args.kwargs["output"]["uri"] == "s3://bronze/test"


@pytest.mark.asyncio
async def test_enqueue_rejects_invalid_policy_without_leaking_input():
    repo = repository()
    raw = policy().model_dump(mode="json")
    raw["password"] = "secret"
    with pytest.raises(AppException) as exc:
        await IngestionExecutionService(repo).start(
            IngestionStart(
                data_source_id=1, dataset_id=2, source_table="input.csv", validation_policy=raw
            ),
            {"roles": ["ADMIN"]},
        )
    assert exc.value.code == "VALIDATION_POLICY_INVALID"
    assert "secret" not in str(exc.value)
    assert repo.create.call_args.kwargs["status"] == "REJECTED"


@pytest.mark.parametrize("failure", [None, "size", "version"])
def test_minio_validated_download_is_bounded_and_version_pinned(monkeypatch, failure):
    from app.modules.ingestion.input_validator import InputValidationError
    from app.modules.ingestion.readers.minio_file_resolver import resolve_file_request

    client = MagicMock()
    raw = b"id,name\n1,Name\n"
    client.stat_object.return_value = SimpleNamespace(
        version_id="v1", size=2_000_000 if failure == "size" else len(raw)
    )
    response = client.get_object.return_value
    response.stream.return_value = iter([raw])
    response.headers = {"x-amz-version-id": "v2" if failure == "version" else "v1"}
    monkeypatch.setattr(
        "app.modules.ingestion.readers.minio_file_resolver._create_minio_client", lambda _: client
    )
    req = ReadRequest(
        "CSV",
        "s3://source/input.csv",
        {},
        "input.csv",
        validation_policy=policy(),
        ingestion_job_id=10,
    )
    if failure:
        with pytest.raises(InputValidationError), resolve_file_request(req):
            pass
        if failure == "size":
            client.get_object.assert_not_called()
    else:
        with resolve_file_request(req) as resolved:
            from pathlib import Path

            assert Path(resolved.location).read_bytes() == raw
            assert resolved.object_version_id == "v1"
        client.get_object.assert_called_once_with("source", "input.csv", version_id="v1")
    if failure != "size":
        response.close.assert_called_once()
        response.release_conn.assert_called_once()


def test_pg_source_revision_change_blocks_old_job():
    ctx = context()
    ctx[0].source_type = "POSTGRESQL"
    ctx[0].updated_at = datetime(2026, 10, 22)
    config = {
        "host": "host",
        "port": 5432,
        "database": "db",
        "username": "reader",
        "password": "secret",
        "connection_config_version": "v1",
    }
    req = ReadRequest("POSTGRESQL", None, config, "public.demo")
    selected = policy(
        parser=PostgreSQLOptions(
            schema_name="public", table_name="demo", connection_config_version="v1"
        ),
        columns=(SourceColumn(name="id", allowed_source_types=("int4",)),),
    )
    snapshot = pin_policy(selected.model_dump(mode="json"), ctx, req)
    assert "secret" not in str(snapshot)
    ctx[0].updated_at = datetime(2026, 10, 23)
    with pytest.raises(ValueError, match="SOURCE_CONFIGURATION_CHANGED"):
        bind_policy(snapshot, ctx, req, 10)


def test_pg_schema_drift_blocks_before_export(monkeypatch):
    conn, req = mock_postgres(monkeypatch, [(1, Decimal("1.20"))])
    req.validation_policy = policy(
        parser=req.validation_policy.parser,
        columns=(
            SourceColumn(name="id", allowed_source_types=("text",)),
            SourceColumn(name="amount", allowed_source_types=("numeric",)),
        ),
    )
    with BronzeCapture() as capture, pytest.raises(ValidationBlockedError) as exc:
        capture.read(req)
    assert exc.value.report.issues[-1].code == "SOURCE_SCHEMA_MISMATCH"
    conn.cursor.assert_not_called()


def test_excel_missing_sheet(tmp_path):
    path = tmp_path / "input.xlsx"
    excel_file(path, [["id", "name"], [1, "Name"]])
    with BronzeCapture() as capture, pytest.raises(ValidationBlockedError) as exc:
        capture.read(request(path, policy(parser=ExcelOptions(sheet_name="Absent"))))
    assert exc.value.report.issues[-1].code == "SHEET_NOT_FOUND"


def test_warning_does_not_hide_later_row_error(tmp_path):
    path = tmp_path / "input.csv"
    path.write_text("id,name,extra\n1,Name\n", encoding="utf-8")
    with BronzeCapture() as capture, pytest.raises(ValidationBlockedError) as exc:
        capture.read(request(path, policy(allow_extra_columns=True)))
    assert exc.value.report.result == "FAIL"
    assert exc.value.report.total_warnings == 1
    assert exc.value.report.total_errors == 1


@pytest.mark.parametrize(
    ("exception_name", "code", "result"),
    [
        ("UndefinedTable", "SOURCE_NOT_FOUND", "FAIL"),
        ("InsufficientPrivilege", "SOURCE_ACCESS_DENIED", "ERROR"),
        ("QueryCanceled", "RESOURCE_LIMIT_EXCEEDED", "FAIL"),
    ],
)
def test_pg_errors_are_sanitized(monkeypatch, exception_name, code, result):
    import psycopg

    _, req = mock_postgres(monkeypatch, [])

    def fail(**_):
        raise getattr(psycopg.errors, exception_name)("password=secret")

    monkeypatch.setattr("app.modules.ingestion.input_validator.psycopg.connect", fail)
    with BronzeCapture() as capture, pytest.raises(ValidationBlockedError) as exc:
        capture.read(req)
    assert exc.value.report.result == result
    assert exc.value.report.issues[-1].code == code
    assert "secret" not in exc.value.report.model_dump_json()
