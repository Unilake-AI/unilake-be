from __future__ import annotations

import hashlib
from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from app.core.config import settings
from app.modules.ingestion.readers.contract import ReadRequest
from app.modules.ingestion.readers.csv_reader import CSVReader
from app.modules.ingestion.readers.file_snapshot import read_file_snapshot
from app.modules.pipeline import quarantine_records as quarantine
from app.modules.pipeline.cleaning_pipeline import CleaningPipeline
from tests.test_manual_cleaning_api import _demo_cleaning_task


def test_records_keep_original_position_and_group_rules_for_same_row():
    task = _demo_cleaning_task("hr")
    original = replace(task.batch, records=({**task.batch.records[0], "id": ""},))
    cleaned = CleaningPipeline().clean(replace(task, batch=original))
    issue = cleaned.report.missing_issues[0]
    report = replace(cleaned.report, missing_issues=(issue, replace(issue, rule_version="2")))
    conn = MagicMock()
    quarantine.save_quarantine_records(conn, "run", original, replace(cleaned, report=report))

    [saved] = conn.execute.call_args.args[1]
    assert saved["record_position"] == 1
    assert saved["locator"] == "record:1"
    assert saved["error_codes"] == [issue.code]
    assert [entry["rule_version"] for entry in saved["issues"]] == [issue.rule_version, "2"]
    assert "data" not in saved


@pytest.mark.parametrize(
    ("actual_version", "tampered", "expected_error"),
    [
        ("pinned", False, None),
        ("newer", False, "QUARANTINE_SOURCE_VERSION_MISMATCH"),
        ("pinned", True, "QUARANTINE_SOURCE_CHECKSUM_MISMATCH"),
    ],
)
def test_source_reads_pinned_csv_record_and_rejects_wrong_bytes(
    monkeypatch, tmp_path, actual_version, tampered, expected_error
):
    content = b'id,name\n001,"first\nrecord"\n002,second\n'
    path = tmp_path / "input.csv"
    path.write_bytes(content)
    options = {"delimiter": ",", "encoding": "utf-8", "has_header": True}
    parsed = read_file_snapshot(
        ReadRequest("CSV", str(path), options, path.name),
        CSVReader(),
        source_system_id="1",
        data_source_id="2",
        dataset_id="3",
    )
    assert parsed.provenance is not None
    record = {
        "locator": "record:2",
        "record_position": 2,
        "quarantine_source": {
            "uri": f"s3://{settings.minio_bucket_bronze}/hr/job-1/{path.name}",
            "version_id": "pinned",
            "file_name": path.name,
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
            "source_type": "CSV",
            "parser_options": options,
            "source_system_id": "1",
            "data_source_id": "2",
            "dataset_id": "3",
            "parser_fingerprint": parsed.provenance.parser_fingerprint,
            "columns_fingerprint": parsed.provenance.columns_fingerprint,
        },
    }
    storage = MagicMock()
    response = storage.get_object.return_value
    response.headers = {"x-amz-version-id": actual_version}
    response.stream.return_value = [content.replace(b"second", b"forged") if tampered else content]
    monkeypatch.setattr(quarantine, "bronze_reader_client", lambda **_: storage)

    if expected_error:
        with pytest.raises(ValueError, match=expected_error):
            quarantine.read_quarantine_source(record)
    else:
        assert quarantine.read_quarantine_source(record) == {"id": "002", "name": "second"}
    storage.get_object.assert_called_once_with(
        settings.minio_bucket_bronze, "hr/job-1/input.csv", version_id="pinned"
    )
    response.release_conn.assert_called_once()
